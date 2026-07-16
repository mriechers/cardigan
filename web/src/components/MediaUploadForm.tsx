import { useCallback, useEffect, useRef, useState } from 'react'
import { useToast } from './ui/Toast'

interface MediaUploadFormProps {
  onUploadComplete?: () => void
}

interface MediaUploadResponse {
  job_id: number
  media_file: string
  original_filename: string
  audio_extracted: boolean
  duration_seconds: number | null
  glossary_terms_added: number
}

interface GlossarySummary {
  whisper_terms: string[]
  whisper_term_count: number
  correction_count: number
}

const AUDIO_EXTENSIONS = ['.wav', '.mp3', '.m4a', '.flac']
const VIDEO_EXTENSIONS = ['.mp4', '.mkv', '.mov', '.webm']
const MEDIA_EXTENSIONS = [...AUDIO_EXTENSIONS, ...VIDEO_EXTENSIONS]
const MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024 // 2 GB (server-enforced too)

/** Chip-list input: type a value, Enter/comma adds it, click ✕ removes. */
function ChipInput({
  id,
  label,
  hint,
  values,
  onChange,
  placeholder,
}: {
  id: string
  label: string
  hint?: string
  values: string[]
  onChange: (values: string[]) => void
  placeholder: string
}) {
  const [draft, setDraft] = useState('')

  const commit = () => {
    const cleaned = draft.trim().replace(/,$/, '')
    if (!cleaned) return
    if (!values.some(v => v.toLowerCase() === cleaned.toLowerCase())) {
      onChange([...values, cleaned])
    }
    setDraft('')
  }

  return (
    <div>
      <label htmlFor={id} className="block text-sm font-medium text-surface-300">
        {label}
      </label>
      {hint && <p className="text-xs text-surface-400 mt-0.5">{hint}</p>}
      {values.length > 0 && (
        <ul className="flex flex-wrap gap-1.5 mt-2" aria-label={`${label} added`}>
          {values.map((value, idx) => (
            <li
              key={value}
              className="flex items-center gap-1 bg-pbs-500/20 text-pbs-300 border border-pbs-500/30 rounded-full px-2.5 py-0.5 text-sm"
            >
              <span>{value}</span>
              <button
                type="button"
                onClick={() => onChange(values.filter((_, i) => i !== idx))}
                className="text-pbs-300 hover:text-white transition-colors"
                aria-label={`Remove ${value}`}
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}
      <input
        id={id}
        type="text"
        value={draft}
        placeholder={placeholder}
        onChange={e => {
          if (e.target.value.endsWith(',')) {
            setDraft(e.target.value)
            // Commit on comma
            const cleaned = e.target.value.slice(0, -1).trim()
            if (cleaned && !values.some(v => v.toLowerCase() === cleaned.toLowerCase())) {
              onChange([...values, cleaned])
            }
            setDraft('')
          } else {
            setDraft(e.target.value)
          }
        }}
        onKeyDown={e => {
          if (e.key === 'Enter') {
            e.preventDefault()
            commit()
          }
        }}
        onBlur={commit}
        className="mt-2 w-full bg-surface-900 border border-surface-600 rounded-md px-3 py-2 text-sm text-white placeholder-surface-400 focus:outline-none focus:border-pbs-400"
      />
    </div>
  )
}

export default function MediaUploadForm({ onUploadComplete }: MediaUploadFormProps) {
  const [file, setFile] = useState<File | null>(null)
  const [projectName, setProjectName] = useState('')
  const [speakers, setSpeakers] = useState<string[]>([])
  const [contextTerms, setContextTerms] = useState<string[]>([])
  const [addToGlossary, setAddToGlossary] = useState(false)
  const [showGlossary, setShowGlossary] = useState(false)
  const [glossary, setGlossary] = useState<GlossarySummary | null>(null)
  const [isDragging, setIsDragging] = useState(false)
  const [isUploading, setIsUploading] = useState(false)
  const [progress, setProgress] = useState(0)
  const [result, setResult] = useState<MediaUploadResponse | null>(null)
  const xhrRef = useRef<XMLHttpRequest | null>(null)
  const { toast } = useToast()

  useEffect(() => {
    fetch('/api/glossary')
      .then(r => (r.ok ? r.json() : null))
      .then(data => data && setGlossary(data))
      .catch(() => setGlossary(null))
  }, [])

  const acceptFile = useCallback(
    (candidate: File | undefined) => {
      if (!candidate) return
      const ext = '.' + candidate.name.split('.').pop()?.toLowerCase()
      if (!MEDIA_EXTENSIONS.includes(ext)) {
        toast(`Unsupported media type. Allowed: ${MEDIA_EXTENSIONS.join(', ')}`, 'error')
        return
      }
      if (candidate.size > MAX_FILE_SIZE) {
        toast('File too large. Maximum: 2 GB', 'error')
        return
      }
      setFile(candidate)
      setResult(null)
      if (!projectName) {
        setProjectName(candidate.name.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ').trim())
      }
    },
    [projectName, toast]
  )

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      setIsDragging(false)
      acceptFile(e.dataTransfer.files?.[0])
    },
    [acceptFile]
  )

  const upload = () => {
    if (!file || !projectName.trim() || isUploading) return

    setIsUploading(true)
    setProgress(0)
    setResult(null)

    const formData = new FormData()
    formData.append('file', file)
    formData.append(
      'intake',
      JSON.stringify({
        project_name: projectName.trim(),
        speakers,
        context_terms: contextTerms,
        add_to_glossary: addToGlossary,
        language: 'en',
      })
    )

    // XMLHttpRequest for real upload-progress events (fetch has none)
    const xhr = new XMLHttpRequest()
    xhrRef.current = xhr
    xhr.open('POST', '/api/upload/media')
    xhr.upload.onprogress = e => {
      if (e.lengthComputable) setProgress(Math.round((e.loaded / e.total) * 100))
    }
    xhr.onload = () => {
      setIsUploading(false)
      xhrRef.current = null
      if (xhr.status >= 200 && xhr.status < 300) {
        const data: MediaUploadResponse = JSON.parse(xhr.responseText)
        setResult(data)
        setFile(null)
        toast(
          `Queued for transcription${data.glossary_terms_added ? ` — ${data.glossary_terms_added} glossary terms added` : ''}`,
          'success'
        )
        onUploadComplete?.()
      } else {
        let message = `Upload failed (${xhr.status})`
        try {
          message = JSON.parse(xhr.responseText).detail || message
        } catch {
          // non-JSON error body
        }
        toast(message, 'error')
      }
    }
    xhr.onerror = () => {
      setIsUploading(false)
      xhrRef.current = null
      toast('Upload failed — network error', 'error')
    }
    xhr.send(formData)
  }

  const cancelUpload = () => {
    xhrRef.current?.abort()
    xhrRef.current = null
    setIsUploading(false)
    setProgress(0)
    toast('Upload cancelled', 'warning')
  }

  return (
    <div className="bg-surface-800 rounded-lg border border-surface-700 p-6 space-y-4">
      <div>
        <h3 className="text-lg font-semibold text-white">Upload Audio or Video</h3>
        <p className="text-sm text-surface-400 mt-1">
          The audio track is transcribed with speaker detection, then you review and correct the
          transcript before the metadata pipeline runs. Video uploads keep only the audio.
        </p>
      </div>

      {/* Drop zone / selected file */}
      {file ? (
        <div className="flex items-center justify-between bg-surface-900 rounded-lg px-4 py-3">
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-surface-300 truncate">{file.name}</span>
            <span className="text-surface-400 text-xs whitespace-nowrap">
              ({(file.size / 1024 / 1024).toFixed(1)} MB)
            </span>
          </div>
          {!isUploading && (
            <button
              onClick={() => setFile(null)}
              className="text-surface-400 hover:text-status-failed transition-colors"
              aria-label="Remove file"
            >
              ✕
            </button>
          )}
        </div>
      ) : (
        <div
          onDragOver={e => {
            e.preventDefault()
            setIsDragging(true)
          }}
          onDragLeave={e => {
            e.preventDefault()
            setIsDragging(false)
          }}
          onDrop={handleDrop}
          className={`border-2 border-dashed rounded-lg p-8 text-center transition-colors ${
            isDragging
              ? 'border-pbs-500 bg-pbs-400/10'
              : 'border-surface-600 hover:border-surface-400 bg-surface-900/50'
          }`}
        >
          <div className="space-y-2">
            <p className="text-surface-300">Drag and drop an audio or video file here, or</p>
            <label className="inline-block">
              <input
                type="file"
                accept={MEDIA_EXTENSIONS.join(',')}
                onChange={e => acceptFile(e.target.files?.[0])}
                className="hidden"
                disabled={isUploading}
              />
              <span className="px-4 py-2 bg-pbs-500 hover:bg-pbs-400 text-white rounded-lg cursor-pointer transition-colors inline-block">
                Browse files
              </span>
            </label>
            <p className="text-sm text-surface-400">
              {MEDIA_EXTENSIONS.join(', ')} — up to 2 GB
            </p>
          </div>
        </div>
      )}

      {/* Intake form */}
      <div className="space-y-4">
        <div>
          <label htmlFor="media-project-name" className="block text-sm font-medium text-surface-300">
            Project name
          </label>
          <input
            id="media-project-name"
            type="text"
            value={projectName}
            onChange={e => setProjectName(e.target.value)}
            placeholder="e.g. Here And Now 2318"
            disabled={isUploading}
            className="mt-2 w-full bg-surface-900 border border-surface-600 rounded-md px-3 py-2 text-sm text-white placeholder-surface-400 focus:outline-none focus:border-pbs-400"
          />
        </div>

        <ChipInput
          id="media-speakers"
          label="Speakers"
          hint="Who talks in this recording, most prominent first — used to prompt the transcriber and label speakers."
          values={speakers}
          onChange={setSpeakers}
          placeholder="Type a name and press Enter"
        />

        <ChipInput
          id="media-context-terms"
          label="Context terms"
          hint="Topic terms and proper nouns the transcriber tends to mishear (places, programs, jargon)."
          values={contextTerms}
          onChange={setContextTerms}
          placeholder="Type a term and press Enter"
        />

        <div className="flex items-start gap-2">
          <input
            id="media-add-glossary"
            type="checkbox"
            checked={addToGlossary}
            onChange={e => setAddToGlossary(e.target.checked)}
            disabled={isUploading}
            className="mt-1 accent-pbs-500"
          />
          <div>
            <label htmlFor="media-add-glossary" className="text-sm text-surface-300">
              Add these speakers and terms to the running glossary
            </label>
            <p className="text-xs text-surface-400">
              Glossary terms are included in every future transcription prompt.
              {glossary && (
                <>
                  {' '}
                  <button
                    type="button"
                    onClick={() => setShowGlossary(v => !v)}
                    className="text-pbs-400 hover:text-pbs-300 underline"
                    aria-expanded={showGlossary}
                  >
                    {showGlossary ? 'Hide' : 'View'} current glossary ({glossary.whisper_term_count} terms)
                  </button>
                </>
              )}
            </p>
          </div>
        </div>

        {showGlossary && glossary && (
          <div className="bg-surface-900 rounded-md p-3 max-h-40 overflow-y-auto">
            <ul className="flex flex-wrap gap-1.5">
              {glossary.whisper_terms.map(term => (
                <li
                  key={term}
                  className="bg-surface-700/60 text-surface-300 rounded-full px-2.5 py-0.5 text-xs"
                >
                  {term}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {/* Progress + submit */}
      <div aria-live="polite">
        {isUploading && (
          <div className="space-y-2">
            <div
              className="w-full bg-surface-900 rounded-full h-2 overflow-hidden"
              role="progressbar"
              aria-valuenow={progress}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label="Upload progress"
            >
              <div
                className="bg-pbs-500 h-2 rounded-full transition-all"
                style={{ width: `${progress}%` }}
              />
            </div>
            <div className="flex items-center justify-between text-sm text-surface-400">
              <span>Uploading… {progress}%</span>
              <button onClick={cancelUpload} className="text-surface-400 hover:text-status-failed">
                Cancel
              </button>
            </div>
          </div>
        )}
        {result && (
          <p className="text-sm text-surface-300">
            <span className="text-status-completed">✓</span> Queued as{' '}
            <a href={`/jobs/${result.job_id}`} className="text-pbs-400 hover:text-pbs-300">
              Job #{result.job_id}
            </a>
            {result.audio_extracted && ' — audio track extracted from video'}
            {'. '}
            You&apos;ll review the transcript here once transcription finishes.
          </p>
        )}
      </div>

      {!isUploading && (
        <button
          onClick={upload}
          disabled={!file || !projectName.trim()}
          className={`w-full py-2 px-4 rounded-lg font-medium transition-colors ${
            !file || !projectName.trim()
              ? 'bg-surface-700 text-surface-400 cursor-not-allowed'
              : 'bg-pbs-500 hover:bg-pbs-400 text-white'
          }`}
        >
          Upload and transcribe
        </button>
      )}
    </div>
  )
}
