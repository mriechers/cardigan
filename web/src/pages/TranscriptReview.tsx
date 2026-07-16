import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import AudioBar from '../components/review/AudioBar'
import SegmentList from '../components/review/SegmentList'
import { EditableSegment } from '../components/review/SegmentRow'
import SpeakerMapPanel from '../components/review/SpeakerMapPanel'
import ConfirmDialog from '../components/ui/ConfirmDialog'
import Modal from '../components/ui/Modal'
import { useToast } from '../components/ui/Toast'

interface TranscriptionReview {
  job_id: number
  status: string
  raw_segments: Array<{ id: number; text: string }>
  edited: {
    segments: EditableSegment[]
    speaker_map: Record<string, string>
  }
  diarized: boolean
  language: string | null
  duration_seconds: number | null
  intake: {
    speakers?: string[]
    context_terms?: string[]
  }
}

type SaveState = 'saved' | 'dirty' | 'saving' | 'error'

const AUTOSAVE_DELAY_MS = 2000

export default function TranscriptReview() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { toast } = useToast()

  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [status, setStatus] = useState<string>('')
  const [segments, setSegments] = useState<EditableSegment[]>([])
  const [speakerMap, setSpeakerMap] = useState<Record<string, string>>({})
  const [rawById, setRawById] = useState<Record<number, string>>({})
  const [intakeSpeakers, setIntakeSpeakers] = useState<string[]>([])
  const [diarized, setDiarized] = useState(true)
  const [saveState, setSaveState] = useState<SaveState>('saved')
  const [currentTime, setCurrentTime] = useState(0)
  const [showApprove, setShowApprove] = useState(false)
  const [showRetranscribe, setShowRetranscribe] = useState(false)
  const [extraTerms, setExtraTerms] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const audioRef = useRef<HTMLAudioElement>(null)
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  // Latest document for the debounced save without re-arming the timer
  const docRef = useRef({ segments, speakerMap })
  docRef.current = { segments, speakerMap }

  const readOnly = status !== 'awaiting_review'

  useEffect(() => {
    let cancelled = false
    fetch(`/api/jobs/${id}/transcription`)
      .then(async r => {
        if (!r.ok) {
          const detail = (await r.json().catch(() => null))?.detail
          throw new Error(detail || `Failed to load transcription (${r.status})`)
        }
        return r.json()
      })
      .then((data: TranscriptionReview) => {
        if (cancelled) return
        setStatus(data.status)
        setSegments(data.edited.segments)
        setSpeakerMap(data.edited.speaker_map)
        setRawById(
          Object.fromEntries(data.raw_segments.map(seg => [seg.id, (seg.text || '').trim()]))
        )
        setIntakeSpeakers(data.intake.speakers || [])
        setDiarized(data.diarized)
        setLoading(false)
      })
      .catch(err => {
        if (cancelled) return
        setLoadError(err instanceof Error ? err.message : 'Failed to load transcription')
        setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [id])

  const persist = useCallback(async () => {
    setSaveState('saving')
    try {
      const response = await fetch(`/api/jobs/${id}/transcription`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          segments: docRef.current.segments,
          speaker_map: docRef.current.speakerMap,
        }),
      })
      if (!response.ok) throw new Error(`Save failed (${response.status})`)
      setSaveState('saved')
    } catch {
      setSaveState('error')
    }
  }, [id])

  const scheduleSave = useCallback(() => {
    setSaveState('dirty')
    if (saveTimer.current) clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(persist, AUTOSAVE_DELAY_MS)
  }, [persist])

  useEffect(() => () => {
    if (saveTimer.current) clearTimeout(saveTimer.current)
  }, [])

  const handleTextChange = useCallback(
    (segmentId: number, text: string) => {
      setSegments(prev => prev.map(s => (s.id === segmentId ? { ...s, text } : s)))
      scheduleSave()
    },
    [scheduleSave]
  )

  const handleSpeakerChange = useCallback(
    (segmentId: number, speaker: string) => {
      setSegments(prev => prev.map(s => (s.id === segmentId ? { ...s, speaker } : s)))
      scheduleSave()
    },
    [scheduleSave]
  )

  const handleNameChange = useCallback(
    (label: string, name: string) => {
      setSpeakerMap(prev => ({ ...prev, [label]: name }))
      scheduleSave()
    },
    [scheduleSave]
  )

  const seek = useCallback((seconds: number) => {
    const audio = audioRef.current
    if (audio) {
      audio.currentTime = seconds
      audio.play().catch(() => undefined)
    }
  }, [])

  const speakerLabels = useMemo(() => Object.keys(speakerMap).sort(), [speakerMap])

  const segmentCounts = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const seg of segments) {
      if (seg.speaker) counts[seg.speaker] = (counts[seg.speaker] || 0) + 1
    }
    return counts
  }, [segments])

  const firstSegmentStart = useMemo(() => {
    const firsts: Record<string, number> = {}
    for (const seg of segments) {
      if (seg.speaker && !(seg.speaker in firsts)) firsts[seg.speaker] = seg.start
    }
    return firsts
  }, [segments])

  const activeSegmentId = useMemo(() => {
    const active = segments.find(s => currentTime >= s.start && currentTime < s.end)
    return active ? active.id : null
  }, [segments, currentTime])

  const editedCount = useMemo(
    () => segments.filter(s => (rawById[s.id] ?? s.text.trim()) !== s.text.trim()).length,
    [segments, rawById]
  )

  const flushPendingSave = async () => {
    if (saveTimer.current) {
      clearTimeout(saveTimer.current)
      saveTimer.current = null
    }
    if (saveState !== 'saved') await persist()
  }

  const approve = async () => {
    setSubmitting(true)
    try {
      await flushPendingSave()
      const response = await fetch(`/api/jobs/${id}/transcription/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ update_glossary: true }),
      })
      if (!response.ok) {
        const detail = (await response.json().catch(() => null))?.detail
        throw new Error(detail || `Approve failed (${response.status})`)
      }
      const data = await response.json()
      toast(
        `Transcript approved${data.corrections_added ? ` — ${data.corrections_added} corrections added to the glossary` : ''}. Pipeline queued.`,
        'success'
      )
      navigate(`/jobs/${id}`)
    } catch (err) {
      toast(err instanceof Error ? err.message : 'Approve failed', 'error')
    } finally {
      setSubmitting(false)
      setShowApprove(false)
    }
  }

  const retranscribe = async () => {
    setSubmitting(true)
    try {
      const terms = extraTerms
        .split(',')
        .map(t => t.trim())
        .filter(Boolean)
      const response = await fetch(`/api/jobs/${id}/transcription/retranscribe`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ extra_terms: terms }),
      })
      if (!response.ok) {
        const detail = (await response.json().catch(() => null))?.detail
        throw new Error(detail || `Re-transcribe failed (${response.status})`)
      }
      toast('Re-transcription queued — current edits were discarded.', 'success')
      navigate(`/jobs/${id}`)
    } catch (err) {
      toast(err instanceof Error ? err.message : 'Re-transcribe failed', 'error')
    } finally {
      setSubmitting(false)
      setShowRetranscribe(false)
    }
  }

  if (loading) {
    return <div className="py-24 text-center text-surface-400 text-sm">Loading transcript…</div>
  }

  if (loadError) {
    return (
      <div className="py-24 text-center space-y-3">
        <p className="text-surface-300">{loadError}</p>
        <Link to={`/jobs/${id}`} className="text-pbs-400 hover:text-pbs-300 text-sm">
          ← Back to job
        </Link>
      </div>
    )
  }

  const saveLabel: Record<SaveState, string> = {
    saved: 'All changes saved',
    dirty: 'Unsaved changes…',
    saving: 'Saving…',
    error: 'Save failed — edits are kept locally, retrying on next change',
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link to={`/jobs/${id}`} className="text-sm text-pbs-400 hover:text-pbs-300">
            ← Job #{id}
          </Link>
          <h1 className="text-xl font-semibold text-white mt-1">Review transcript</h1>
          <p className="text-sm text-surface-400 mt-1">
            Correct the text and name the speakers. Approving hands the transcript to the metadata
            pipeline; your corrections also teach the transcriber for future uploads.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-surface-400" aria-live="polite">
            {saveLabel[saveState]}
          </span>
          <button
            onClick={() => setShowRetranscribe(true)}
            disabled={readOnly || submitting}
            className="px-3 py-1.5 text-sm bg-surface-700 hover:bg-surface-600 text-surface-200 rounded-md transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Re-transcribe
          </button>
          <button
            onClick={() => setShowApprove(true)}
            disabled={readOnly || submitting}
            className="px-3 py-1.5 text-sm bg-pbs-500 hover:bg-pbs-400 text-white rounded-md transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Approve &amp; process
          </button>
        </div>
      </div>

      {readOnly && (
        <div className="bg-surface-800 border border-surface-700 rounded-lg px-4 py-3 text-sm text-surface-300">
          This job is <span className="font-medium">{status.replace('_', ' ')}</span> — the
          transcript is shown read-only.
        </div>
      )}

      {!diarized && (
        <div className="bg-surface-800 border border-surface-700 rounded-lg px-4 py-3 text-sm text-surface-300">
          Speaker detection wasn&apos;t available for this run, so all segments share one speaker
          bucket. You can still name it, and the name will label the whole transcript.
        </div>
      )}

      <AudioBar ref={audioRef} src={`/api/jobs/${id}/media`} onTimeUpdate={setCurrentTime} />

      <div className="grid grid-cols-1 lg:grid-cols-[280px_1fr] gap-4 items-start">
        <div className="lg:sticky lg:top-16 space-y-3">
          <SpeakerMapPanel
            speakerMap={speakerMap}
            suggestions={intakeSpeakers}
            segmentCounts={segmentCounts}
            firstSegmentStart={firstSegmentStart}
            disabled={readOnly}
            onNameChange={handleNameChange}
            onPlaySample={seek}
          />
          {editedCount > 0 && (
            <p className="text-xs text-surface-400 px-1">
              {editedCount} segment{editedCount !== 1 ? 's' : ''} edited
            </p>
          )}
        </div>

        <div className="bg-surface-800 rounded-lg border border-surface-700 p-4">
          <SegmentList
            segments={segments}
            rawById={rawById}
            speakerLabels={speakerLabels}
            speakerMap={speakerMap}
            activeSegmentId={activeSegmentId}
            disabled={readOnly}
            onTextChange={handleTextChange}
            onSpeakerChange={handleSpeakerChange}
            onSeek={seek}
          />
        </div>
      </div>

      <ConfirmDialog
        isOpen={showApprove}
        onCancel={() => setShowApprove(false)}
        onConfirm={approve}
        title="Approve transcript?"
        message={
          <>
            The corrected transcript is handed to the metadata pipeline (analysis, formatting, SEO,
            validation), and your corrections are mined into the glossary to improve future
            transcriptions. You can still replace the transcript later if needed.
          </>
        }
        confirmText={submitting ? 'Approving…' : 'Approve & process'}
        variant="info"
      />

      <Modal
        isOpen={showRetranscribe}
        onClose={() => setShowRetranscribe(false)}
        title="Re-transcribe recording"
      >
        <div className="space-y-4">
          <p className="text-sm text-surface-300">
            Runs transcription again from the original audio.{' '}
            <span className="text-status-pending">Your current edits will be discarded.</span> Add
            terms below to help the transcriber with names it misheard.
          </p>
          <div>
            <label htmlFor="retranscribe-terms" className="block text-sm font-medium text-surface-300">
              Extra prompt terms (comma-separated)
            </label>
            <input
              id="retranscribe-terms"
              type="text"
              value={extraTerms}
              onChange={e => setExtraTerms(e.target.value)}
              placeholder="e.g. Protasiewicz, Oconomowoc"
              className="mt-2 w-full bg-surface-900 border border-surface-600 rounded-md px-3 py-2 text-sm text-white placeholder-surface-500 focus:outline-none focus:border-pbs-400"
            />
          </div>
          <div className="flex justify-end gap-3">
            <button
              onClick={() => setShowRetranscribe(false)}
              className="px-4 py-2 text-sm font-medium text-surface-300 bg-surface-700 hover:bg-surface-600 rounded-md transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={retranscribe}
              disabled={submitting}
              className="px-4 py-2 text-sm font-medium text-white bg-pbs-500 hover:bg-pbs-400 rounded-md transition-colors disabled:opacity-50"
            >
              {submitting ? 'Queueing…' : 'Re-transcribe'}
            </button>
          </div>
        </div>
      </Modal>
    </div>
  )
}
