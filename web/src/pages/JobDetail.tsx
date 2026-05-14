import { useCallback, useEffect, useState, useRef, Fragment } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useFocusTrap } from '../hooks/useFocusTrap'
import ProseContainer from '../components/ProseContainer'
import { useToast } from '../components/ui/Toast'
import { Skeleton } from '../components/ui/Skeleton'
import { formatRelativeTime, formatTimestamp, formatDuration } from '../utils/formatTime'
import ScreengrabSlideout from '../components/ScreengrabSlideout'
import ScreengrabsBox from '../components/ScreengrabsBox'

interface PreviousRun {
  model?: string
  cost?: number
  tokens?: number
  input_tokens?: number
  output_tokens?: number
  completed_at?: string
  feedback?: string
}

interface JobPhase {
  name: string
  status: string
  cost?: number
  tokens?: number
  input_tokens?: number
  output_tokens?: number
  started_at?: string
  completed_at?: string
  model?: string
  attempts?: number
  retry_count: number
  previous_runs?: PreviousRun[]
}

interface JobOutputs {
  analysis?: string
  formatted_transcript?: string
  seo_metadata?: string
  qa_review?: string
  timestamp_report?: string
  copy_edited?: string
  recovery_analysis?: string
}

interface SSTMetadata {
  media_id?: string
  release_title?: string
  short_description?: string
  media_manager_url?: string
  youtube_url?: string
  airtable_url?: string
}

interface JobDetail {
  id: number
  project_name: string
  transcript_file?: string
  status: string
  priority: number
  queued_at: string
  started_at?: string
  completed_at?: string
  current_phase?: string
  last_heartbeat?: string
  retry_count: number
  max_retries: number
  phases?: JobPhase[]
  actual_cost?: number
  total_tokens?: number
  error_message?: string
  outputs?: JobOutputs
  airtable_record_id?: string
  airtable_url?: string
  media_id?: string
  content_type?: string
  validation_result?: {
    phase_results: Record<string, { status: 'pass' | 'fail'; flags: string[] }>
    overall: 'pass' | 'fail'
  }
}

// Map output keys to their display names and filenames
const OUTPUT_FILES: Record<string, { label: string; filename: string }> = {
  analysis: { label: 'Analysis', filename: 'analyst_output.md' },
  formatted_transcript: { label: 'Formatted Transcript', filename: 'formatter_output.md' },
  seo_metadata: { label: 'SEO Metadata', filename: 'seo_output.md' },
  qa_review: { label: 'Validation', filename: 'validator_output.md' },
  timestamp_report: { label: 'Timestamps', filename: 'timestamp_output.md' },
  copy_edited: { label: 'Copy Edited', filename: 'copy_editor_output.md' },
  recovery_analysis: { label: 'Recovery Analysis', filename: 'recovery_analysis.md' },
}

const OUTPUT_TO_PHASE: Record<string, string> = {
  analysis: 'analyst',
  formatted_transcript: 'formatter',
  seo_metadata: 'seo',
  timestamp_report: 'timestamp',
}

function CostBreakdownTable({ phases }: { phases: JobPhase[] }) {
  const phasesWithCost = phases.filter(p => p.cost !== undefined && p.cost > 0)
  if (phasesWithCost.length === 0) return null

  const totalCost = phasesWithCost.reduce((sum, p) => {
    let phaseCost = p.cost || 0
    if (p.previous_runs) {
      phaseCost += p.previous_runs.reduce((s, r) => s + (r.cost || 0), 0)
    }
    return sum + phaseCost
  }, 0)

  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700 p-4">
      <h2 className="text-lg font-medium text-white mb-3">Cost Breakdown</h2>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-gray-500 text-left">
            <th className="pb-2">Phase</th>
            <th className="pb-2">Model</th>
            <th className="pb-2 text-right">In Tokens</th>
            <th className="pb-2 text-right">Out Tokens</th>
            <th className="pb-2 text-right">Cost</th>
          </tr>
        </thead>
        <tbody className="text-gray-300">
          {phasesWithCost.map(phase => (
            <Fragment key={phase.name}>
              {phase.previous_runs?.map((run, i) => (
                <tr key={`${phase.name}-prev-${i}`} className="text-gray-400 text-xs">
                  <td className="py-1 pl-4">{phase.name} (attempt {i + 1})</td>
                  <td className="py-1 font-mono">{run.model?.split('/').pop() || '-'}</td>
                  <td className="py-1 text-right font-mono">{run.input_tokens?.toLocaleString() || '-'}</td>
                  <td className="py-1 text-right font-mono">{run.output_tokens?.toLocaleString() || '-'}</td>
                  <td className="py-1 text-right font-mono">${(run.cost || 0).toFixed(4)}</td>
                </tr>
              ))}
              <tr>
                <td className="py-1">{phase.name}</td>
                <td className="py-1 font-mono">{phase.model?.split('/').pop() || '-'}</td>
                <td className="py-1 text-right font-mono">{phase.input_tokens?.toLocaleString() || '-'}</td>
                <td className="py-1 text-right font-mono">{phase.output_tokens?.toLocaleString() || '-'}</td>
                <td className="py-1 text-right font-mono">${(phase.cost || 0).toFixed(4)}</td>
              </tr>
            </Fragment>
          ))}
          <tr className="border-t border-gray-700 font-medium text-white">
            <td className="pt-2" colSpan={4}>Total</td>
            <td className="pt-2 text-right font-mono">${totalCost.toFixed(4)}</td>
          </tr>
        </tbody>
      </table>
    </div>
  )
}

export default function JobDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [job, setJob] = useState<JobDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [viewingOutput, setViewingOutput] = useState<{
    label: string
    content: string
    isJson: boolean
  } | null>(null)
  const [loadingOutput, setLoadingOutput] = useState(false)
  const [sstMetadata, setSstMetadata] = useState<SSTMetadata | null>(null)
  const [sstLoading, setSstLoading] = useState(false)
  const [retryingPhase, setRetryingPhase] = useState<string | null>(null)
  const [retryModal, setRetryModal] = useState<{ outputKey: string; label: string } | null>(null)
  const [retryFeedback, setRetryFeedback] = useState('')
  const [retryModel, setRetryModel] = useState<string>('')
  const [availableModels, setAvailableModels] = useState<{id: string, name: string}[]>([])
  const [showScreengrabs, setShowScreengrabs] = useState(false)
  const [hasScreengrabs, setHasScreengrabs] = useState(false)
  const [driveConfigured, setDriveConfigured] = useState(false)
  const [uploadingToDrive, setUploadingToDrive] = useState<string | null>(null)
  const [keywordReports, setKeywordReports] = useState<Array<{ filename: string; version: number; uploaded_at?: string }>>([])
  const [keywordUploading, setKeywordUploading] = useState(false)
  const keywordInputRef = useRef<HTMLInputElement | null>(null)
  const triggerRef = useRef<HTMLButtonElement | null>(null)
  const modalRef = useFocusTrap(!!viewingOutput)
  const { toast } = useToast()

  useEffect(() => {
    const fetchJob = async () => {
      try {
        const response = await fetch(`/api/jobs/${id}`)
        if (!response.ok) {
          throw new Error('Job not found')
        }
        setJob(await response.json())
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load job')
      } finally {
        setLoading(false)
      }
    }

    fetchJob()

    // Auto-refresh while job is in progress
    const interval = setInterval(() => {
      if (job?.status === 'in_progress' || job?.status === 'pending') {
        fetchJob()
      }
    }, 3000)

    return () => clearInterval(interval)
  }, [id, job?.status])

  // Fetch SST metadata when job has an airtable_record_id
  useEffect(() => {
    const fetchSstMetadata = async () => {
      if (!job?.airtable_record_id) return

      setSstLoading(true)
      try {
        const response = await fetch(`/api/jobs/${id}/sst-metadata`)
        if (response.ok) {
          setSstMetadata(await response.json())
        }
      } catch (err) {
        // Silently fail - SST metadata is supplementary
        console.error('Failed to fetch SST metadata:', err)
      } finally {
        setSstLoading(false)
      }
    }

    fetchSstMetadata()
  }, [id, job?.airtable_record_id])

  // Fetch keyword reports for this job
  const fetchKeywordReports = useCallback(async () => {
    if (!id) return
    try {
      const response = await fetch(`/api/jobs/${id}/keyword-reports`)
      if (response.ok) {
        const data = await response.json()
        setKeywordReports(data.reports || [])
      }
    } catch (err) {
      // Silently fail
      console.error('Failed to fetch keyword reports:', err)
    }
  }, [id])

  useEffect(() => {
    if (id) fetchKeywordReports()
  }, [id, fetchKeywordReports])

  // Check for available screengrabs when job has a media_id
  useEffect(() => {
    const checkScreengrabs = async () => {
      if (!job?.media_id) {
        setHasScreengrabs(false)
        return
      }

      try {
        const response = await fetch(`/api/ingest/screengrabs/for-media-id/${job.media_id}`)
        if (response.ok) {
          const data = await response.json()
          setHasScreengrabs(data.screengrabs && data.screengrabs.length > 0)
        }
      } catch (err) {
        // Silently fail - screengrab check is supplementary
        console.error('Failed to check screengrabs:', err)
      }
    }

    checkScreengrabs()
  }, [job?.media_id])

  useEffect(() => {
    fetch('/api/config/models')
      .then(res => res.json())
      .then(data => {
        setAvailableModels(data.available_models || [])
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    fetch('/api/export/status')
      .then(res => res.json())
      .then(data => {
        setDriveConfigured(data.google_drive?.configured || false)
      })
      .catch(() => {})
  }, [])

  const handleAction = async (action: string) => {
    const actionLabels: Record<string, { success: string; error: string }> = {
      pause: { success: 'Job paused successfully', error: 'Failed to pause job' },
      resume: { success: 'Job resumed successfully', error: 'Failed to resume job' },
      retry: { success: 'Job retry initiated', error: 'Failed to retry job' },
      cancel: { success: 'Job cancelled successfully', error: 'Failed to cancel job' },
    }

    try {
      const response = await fetch(`/api/jobs/${id}/${action}`, {
        method: 'POST',
      })
      if (response.ok) {
        toast(actionLabels[action]?.success || `Job ${action}ed successfully`, 'success')
        // Refresh job data
        const updated = await fetch(`/api/jobs/${id}`)
        if (updated.ok) {
          setJob(await updated.json())
        }
      } else {
        toast(actionLabels[action]?.error || `Failed to ${action} job`, 'error')
      }
    } catch (err) {
      console.error(`Failed to ${action} job:`, err)
      toast(actionLabels[action]?.error || `Failed to ${action} job`, 'error')
    }
  }

  const handleViewOutput = async (key: string, filename: string, event: React.MouseEvent<HTMLButtonElement>) => {
    // Save reference to trigger button
    triggerRef.current = event.currentTarget

    setLoadingOutput(true)
    try {
      const response = await fetch(`/api/jobs/${id}/outputs/${filename}`)
      if (!response.ok) {
        throw new Error('Failed to load output')
      }
      const content = await response.text()
      const fileInfo = OUTPUT_FILES[key]
      setViewingOutput({
        label: fileInfo?.label || filename,
        content,
        isJson: filename.endsWith('.json'),
      })
    } catch (err) {
      console.error('Failed to load output:', err)
    } finally {
      setLoadingOutput(false)
    }
  }

  const handleRetryPhase = async (outputKey: string, feedback?: string) => {
    setRetryingPhase(outputKey)
    setRetryModal(null)
    setRetryFeedback('')
    try {
      const body: { feedback?: string; model?: string } = {}
      if (feedback && feedback.trim()) body.feedback = feedback.trim()
      if (retryModel) body.model = retryModel

      const response = await fetch(`/api/jobs/${id}/phases/${outputKey}/retry`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (response.ok) {
        toast('Phase retry started. Refresh in a moment to see results.', 'success')
        // Auto-refresh after a delay
        setTimeout(async () => {
          const updated = await fetch(`/api/jobs/${id}`)
          if (updated.ok) {
            setJob(await updated.json())
          }
        }, 5000)
      } else {
        const data = await response.json()
        toast(data.detail || 'Failed to retry phase', 'error')
      }
    } catch (err) {
      console.error('Failed to retry phase:', err)
      toast('Failed to retry phase', 'error')
    } finally {
      setRetryingPhase(null)
    }
  }

  const openRetryModal = (outputKey: string, label: string) => {
    // Pre-populate model with current phase model
    const phaseName = OUTPUT_TO_PHASE[outputKey] || outputKey
    const phase = job?.phases?.find((p: JobPhase) => p.name === phaseName)
    setRetryModel(phase?.model || '')
    setRetryFeedback('')
    setRetryModal({ outputKey, label })
  }

  const submitRetryModal = () => {
    if (!retryModal) return
    handleRetryPhase(retryModal.outputKey, retryFeedback)
  }

  const handleKeywordUpload = async (file: File) => {
    setKeywordUploading(true)
    try {
      const formData = new FormData()
      formData.append('file', file)
      const response = await fetch(`/api/jobs/${id}/keyword-report`, {
        method: 'POST',
        body: formData,
      })
      if (response.ok) {
        const data = await response.json()
        toast(`Keyword report uploaded: ${data.filename}`, 'success')
        await fetchKeywordReports()
      } else {
        const data = await response.json()
        toast(data.detail || 'Failed to upload keyword report', 'error')
      }
    } catch (err) {
      console.error('Failed to upload keyword report:', err)
      toast('Failed to upload keyword report', 'error')
    } finally {
      setKeywordUploading(false)
      if (keywordInputRef.current) keywordInputRef.current.value = ''
    }
  }

  const handleDriveUpload = async (key: string, filename: string) => {
    setUploadingToDrive(key)
    try {
      const folderId = localStorage.getItem('cardigan_drive_folder_id') || ''
      const params = folderId ? `?folder_id=${encodeURIComponent(folderId)}` : ''
      const response = await fetch(`/api/export/google-drive/${id}/${filename}${params}`, {
        method: 'POST',
      })
      if (response.ok) {
        const data = await response.json()
        toast('Uploaded to Google Drive', 'success')
        window.open(data.drive_url, '_blank')
      } else {
        const data = await response.json()
        toast(data.detail || 'Failed to upload to Google Drive', 'error')
      }
    } catch (err) {
      console.error('Failed to upload to Drive:', err)
      toast('Failed to upload to Google Drive', 'error')
    } finally {
      setUploadingToDrive(null)
    }
  }

  const closeModal = () => {
    setViewingOutput(null)
    // Return focus to trigger button
    setTimeout(() => {
      triggerRef.current?.focus()
    }, 0)
  }

  // Handle Escape key to close modal
  useEffect(() => {
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && viewingOutput) {
        closeModal()
      }
    }

    document.addEventListener('keydown', handleEscape)
    return () => document.removeEventListener('keydown', handleEscape)
  }, [viewingOutput])

  const phaseStatusIcon = (status: string) => {
    switch (status) {
      case 'completed':
        return <span className="text-green-400">&#10003;</span>
      case 'in_progress':
        return <span className="text-blue-400 animate-pulse">&#9679;</span>
      case 'failed':
        return <span className="text-red-400">&#10007;</span>
      case 'skipped':
        return <span className="text-gray-400">&#8212;</span>
      default:
        return <span className="text-gray-400">&#9675;</span>
    }
  }

  if (loading) {
    return (
      <div className="space-y-6" aria-label="Loading job details" role="status">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-5 w-96" />
        <div className="bg-gray-800 rounded-lg border border-gray-700 p-4">
          <Skeleton className="h-6 w-48 mb-4" />
          <div className="space-y-3">
            {[1, 2, 3].map((i) => (
              <div key={i}>
                <Skeleton className="h-5 w-full" />
              </div>
            ))}
          </div>
        </div>
        <span className="sr-only">Loading job details...</span>
      </div>
    )
  }

  if (error || !job) {
    return (
      <div className="text-center py-12">
        <div role="alert" aria-live="assertive" className="text-red-400 mb-4">{error || 'Job not found'}</div>
        <button
          onClick={() => navigate(-1)}
          className="text-blue-400 hover:text-blue-300"
        >
          Go back
        </button>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <button
            onClick={() => navigate(-1)}
            className="text-sm text-gray-400 hover:text-white mb-2 inline-block transition-colors"
            aria-label="Go back to previous page"
          >
            &#8592; Back
          </button>
          <div className="flex items-center gap-2">
            <h1 className="text-2xl font-bold text-white">
              {job.project_name}
            </h1>
            {job.content_type === 'short' && (
              <span className="px-2 py-0.5 text-xs font-semibold rounded-full bg-rose-600 text-white">
                Short
              </span>
            )}
            {job.content_type === 'clip' && (
              <span className="px-2 py-0.5 text-xs font-semibold rounded-full bg-amber-600 text-white">
                Clip
              </span>
            )}
            {job.validation_result && (
              <span className={`px-2 py-0.5 text-xs font-semibold rounded-full ${
                job.validation_result.overall === 'pass'
                  ? 'bg-green-900/50 text-green-400 border border-green-800'
                  : 'bg-red-900/50 text-red-400 border border-red-800'
              }`}>
                {job.validation_result.overall === 'pass' ? '\u2713 Validated' : '\u2717 Validation Failed'}
              </span>
            )}
          </div>
          <p className="text-gray-400">
            Job #{job.id}
            {job.current_phase && job.status === 'in_progress' && (
              <span className="ml-2 text-blue-400 animate-pulse">
                • Processing {job.current_phase}...
              </span>
            )}
          </p>
        </div>

        {/* Action Buttons */}
        <div className="flex items-center space-x-2">
          {job.status === 'in_progress' && (
            <button
              onClick={() => handleAction('pause')}
              className="px-3 py-1.5 bg-orange-600 hover:bg-orange-500 text-white rounded-md text-sm"
            >
              Pause
            </button>
          )}
          {job.status === 'paused' && !job.error_message?.includes('TRUNCATION') && (
            <button
              onClick={() => handleAction('resume')}
              className="px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white rounded-md text-sm"
            >
              Resume
            </button>
          )}
          {(job.status === 'failed' || (job.status === 'paused' && job.error_message?.includes('TRUNCATION'))) && (
            <button
              onClick={() => handleAction('retry')}
              className="px-3 py-1.5 bg-green-600 hover:bg-green-500 text-white rounded-md text-sm"
              title="Retry this job"
            >
              Retry
            </button>
          )}
          {['pending', 'paused'].includes(job.status) && (
            <button
              onClick={() => handleAction('cancel')}
              className="px-3 py-1.5 bg-red-600 hover:bg-red-500 text-white rounded-md text-sm"
            >
              Cancel
            </button>
          )}
          {hasScreengrabs && (
            <button
              onClick={() => setShowScreengrabs(!showScreengrabs)}
              className="px-3 py-1.5 bg-purple-600 hover:bg-purple-500 text-white rounded-md text-sm flex items-center space-x-1.5"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
              </svg>
              <span>{showScreengrabs ? 'Close' : 'Screengrabs'}</span>
            </button>
          )}
        </div>
      </div>

      {/* AirTable Metadata Panel */}
      {(job.airtable_url || job.media_id || sstMetadata) && (
        <div className="bg-gray-800 rounded-lg border border-gray-700 p-4">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-medium text-white">AirTable Metadata</h2>
            {job.airtable_url && (
              <a
                href={job.airtable_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center text-sm text-blue-400 hover:text-blue-300 transition-colors"
              >
                <span>Open in AirTable</span>
                <svg
                  className="ml-1.5 w-4 h-4"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"
                  />
                </svg>
              </a>
            )}
          </div>

          {sstLoading ? (
            <div className="text-gray-400 text-sm">Loading metadata...</div>
          ) : (
            <div className="space-y-4">
              {/* Release Title */}
              {sstMetadata?.release_title && (
                <div>
                  <div className="text-xs text-gray-400 uppercase tracking-wide mb-1">
                    Release Title
                  </div>
                  <div className="text-white">{sstMetadata.release_title}</div>
                </div>
              )}

              {/* Short Description */}
              {sstMetadata?.short_description && (
                <div>
                  <div className="text-xs text-gray-400 uppercase tracking-wide mb-1">
                    Short Description
                    <span className="ml-2 text-gray-500">
                      ({sstMetadata.short_description.length}/90 chars)
                    </span>
                  </div>
                  <div className="text-white text-sm">{sstMetadata.short_description}</div>
                </div>
              )}

              {/* Media ID & Links Row */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4 pt-2 border-t border-gray-700">
                {job.media_id && (
                  <div>
                    <div className="text-xs text-gray-400 uppercase tracking-wide mb-1">
                      Media ID
                    </div>
                    <div className="text-white font-mono text-sm">{job.media_id}</div>
                  </div>
                )}

                {sstMetadata?.youtube_url && (
                  <div>
                    <div className="text-xs text-gray-400 uppercase tracking-wide mb-1">
                      YouTube
                    </div>
                    <a
                      href={sstMetadata.youtube_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-red-400 hover:text-red-300 text-sm inline-flex items-center"
                    >
                      Watch
                      <svg className="ml-1 w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                      </svg>
                    </a>
                  </div>
                )}

                {sstMetadata?.media_manager_url && (
                  <div>
                    <div className="text-xs text-gray-400 uppercase tracking-wide mb-1">
                      Website
                    </div>
                    <a
                      href={sstMetadata.media_manager_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-green-400 hover:text-green-300 text-sm inline-flex items-center"
                    >
                      View
                      <svg className="ml-1 w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                      </svg>
                    </a>
                  </div>
                )}

                {job.airtable_record_id && (
                  <div>
                    <div className="text-xs text-gray-400 uppercase tracking-wide mb-1">
                      Record ID
                    </div>
                    <div className="text-gray-500 font-mono text-xs truncate" title={job.airtable_record_id}>
                      {job.airtable_record_id}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Progress Bar for in_progress jobs */}
      {job.status === 'in_progress' && job.phases && (
        <div className="bg-gray-800 rounded-lg border border-gray-700 p-4">
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm text-gray-400">Processing Progress</span>
            <span className="text-sm text-white">
              {job.phases.filter(p => p.status === 'completed').length} / {job.phases.length} phases
            </span>
          </div>
          <div className="w-full bg-gray-700 rounded-full h-2.5">
            <div
              className="bg-blue-500 h-2.5 rounded-full transition-all duration-500"
              style={{
                width: `${(job.phases.filter(p => p.status === 'completed').length / job.phases.length) * 100}%`
              }}
            />
          </div>
        </div>
      )}

      {/* Job Metadata */}
      <div className="flex items-center gap-6 text-sm text-gray-400">
        <span>Status: <span className="text-white">{job.status}</span></span>
        <span>Priority: <span className="text-white">{job.priority}</span></span>
        <span>Cost: <span className="text-green-400 font-mono">{job.actual_cost ? `$${job.actual_cost.toFixed(4)}` : '-'}</span></span>
        <span>Tokens: <span className="text-white">{job.phases?.reduce((sum, p) => sum + (p.tokens || 0), 0).toLocaleString() ?? '-'}</span></span>
      </div>

      {/* Phases */}
      {job.phases && job.phases.length > 0 && (
        <div className="bg-gray-800 rounded-lg border border-gray-700 p-4">
          <h2 className="text-lg font-medium text-white mb-4">
            Processing Phases
          </h2>
          <div className="space-y-3">
            {job.phases.map((phase, idx) => (
              <div
                key={idx}
                className="py-3 border-b border-gray-700 last:border-0"
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center space-x-3">
                    {phaseStatusIcon(phase.status)}
                    <span className="text-white">{phase.name}</span>
                    {job.validation_result?.phase_results?.[phase.name] && (
                      <span className={`ml-2 px-2 py-0.5 rounded text-xs font-medium ${
                        job.validation_result.phase_results[phase.name].status === 'pass'
                          ? 'bg-green-900/30 text-green-400'
                          : 'bg-red-900/30 text-red-400'
                      }`}>
                        {job.validation_result.phase_results[phase.name].status === 'pass' ? '\u2713 Pass' : '\u2717 Fail'}
                      </span>
                    )}
                    {phase.model && (
                      <span className="text-xs px-2 py-0.5 rounded-full bg-gray-700 text-gray-300 font-mono">
                        {phase.model}
                      </span>
                    )}
                    {phase.attempts && phase.attempts > 1 && (
                      <span className="text-xs px-2 py-0.5 rounded-full bg-orange-900/50 text-orange-300">
                        {phase.attempts} attempts
                      </span>
                    )}
                    {phase.retry_count > 0 && (
                      <span
                        className="text-xs px-2 py-0.5 rounded-full bg-amber-900/50 text-amber-300"
                        title={`Retried ${phase.retry_count} time${phase.retry_count > 1 ? 's' : ''}`}
                      >
                        &#8635; {phase.retry_count}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center space-x-4 text-sm text-gray-400">
                    {phase.cost !== undefined && (
                      <span>${phase.cost.toFixed(4)}</span>
                    )}
                    {phase.tokens !== undefined && (
                      <span>{phase.tokens.toLocaleString()} tokens</span>
                    )}
                  </div>
                </div>
                {phase.previous_runs && phase.previous_runs.length > 0 && (
                  <div className="mt-1.5 ml-8 text-xs text-gray-500">
                    <div className="text-gray-600 mb-1">Previous runs:</div>
                    {phase.previous_runs.map((run, i) => (
                      <div key={i} className="flex items-center gap-2 text-gray-500 ml-2">
                        <span className="text-gray-600">#{i + 1}</span>
                        {run.model && <span className="font-mono text-gray-400">{run.model}</span>}
                        <span>${(run.cost || 0).toFixed(4)}</span>
                      </div>
                    ))}
                  </div>
                )}
                {job.validation_result?.phase_results?.[phase.name]?.flags &&
                 job.validation_result.phase_results[phase.name].flags.length > 0 && (
                  <div className="mt-1.5 ml-8 space-y-1">
                    {job.validation_result.phase_results[phase.name].flags.map((flag: string, i: number) => (
                      <div key={i} className="text-xs text-red-400 flex items-start gap-1">
                        <span className="mt-0.5">&#9888;</span>
                        <span>{flag}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Cost Breakdown */}
      {job.phases && job.phases.length > 0 && (
        <CostBreakdownTable phases={job.phases} />
      )}

      {/* Outputs */}
      {job.outputs && Object.keys(job.outputs).length > 0 && (
        <div className="bg-gray-800 rounded-lg border border-gray-700 p-4">
          <h2 className="text-lg font-medium text-white mb-4">
            Output Files
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {Object.entries(job.outputs).map(([key, filename]) => {
              const fileInfo = OUTPUT_FILES[key]
              if (!fileInfo || !filename) return null
              // Use actual filename from API (handles dynamic revision filenames)
              const actualFilename = filename as string
              // For copy_edited, show version if it's a revision file
              const label = key === 'copy_edited' && actualFilename.includes('revision')
                ? `Copy Edited (${actualFilename.match(/v\d+/)?.[0] || ''})`
                : fileInfo.label
              const isRetrying = retryingPhase === key
              return (
                <div key={key} className="flex items-center gap-1">
                  <button
                    onClick={(e) => handleViewOutput(key, actualFilename, e)}
                    disabled={loadingOutput}
                    className="flex-1 flex items-center justify-center px-3 py-2 bg-gray-700 hover:bg-gray-600 rounded-l-md text-sm text-white transition-colors disabled:opacity-50"
                    aria-label={`View ${label}`}
                  >
                    <span className="mr-2">&#128196;</span>
                    {label}
                  </button>
                  <a
                    href={`/api/jobs/${id}/outputs/${actualFilename}?download=true`}
                    download={actualFilename}
                    className="px-2 py-2 bg-gray-600 hover:bg-blue-600 text-gray-300 hover:text-white transition-colors"
                    aria-label={`Download ${label}`}
                    title={`Download ${label}`}
                  >
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                    </svg>
                  </a>
                  <button
                    onClick={() => openRetryModal(key, label)}
                    disabled={isRetrying || retryingPhase !== null}
                    className={`px-2 py-2 bg-gray-600 hover:bg-orange-600 ${driveConfigured ? '' : 'rounded-r-md'} text-sm text-gray-300 hover:text-white transition-colors disabled:opacity-50`}
                    aria-label={`Retry ${label}`}
                    title={`Retry this phase`}
                  >
                    {isRetrying ? (
                      <span className="animate-spin">&#8635;</span>
                    ) : (
                      <span>&#8635;</span>
                    )}
                  </button>
                  {driveConfigured && (
                    <button
                      onClick={() => handleDriveUpload(key, actualFilename)}
                      disabled={uploadingToDrive !== null}
                      className="px-2 py-2 bg-gray-600 hover:bg-green-700 rounded-r-md text-sm text-gray-300 hover:text-white transition-colors disabled:opacity-50"
                      aria-label={`Upload ${label} to Google Drive`}
                      title="Upload to Google Drive"
                    >
                      {uploadingToDrive === key ? (
                        <span className="animate-spin">&#8635;</span>
                      ) : (
                        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                        </svg>
                      )}
                    </button>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Keyword Report Upload — shown when reports exist or job is completed */}
      {(keywordReports.length > 0 || job.status === 'completed') && (
        <div className="bg-gray-800 rounded-lg border border-gray-700 p-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium text-gray-400">SEMRush Keyword Report</h2>
            <div className="flex items-center gap-2">
              <input
                ref={keywordInputRef}
                type="file"
                accept=".csv,.txt,.tsv"
                className="hidden"
                id="keyword-report-input"
                onChange={(e) => {
                  const file = e.target.files?.[0]
                  if (file) handleKeywordUpload(file)
                }}
              />
              <label
                htmlFor="keyword-report-input"
                className={`px-3 py-1.5 bg-gray-700 hover:bg-gray-600 text-gray-300 rounded-md text-sm cursor-pointer transition-colors ${keywordUploading ? 'opacity-50 pointer-events-none' : ''}`}
              >
                {keywordUploading ? 'Uploading...' : 'Upload'}
              </label>
            </div>
          </div>
          {keywordReports.length > 0 && (
            <ul className="mt-2 space-y-1">
              {keywordReports.map((report) => (
                <li key={report.filename} className="flex items-center justify-between text-sm">
                  <span className="text-gray-300 font-mono">{report.filename}</span>
                  {report.uploaded_at && (
                    <span className="text-gray-500 text-xs">{report.uploaded_at}</span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* Screengrabs (inline) */}
      {job.media_id && (
        <ScreengrabsBox mediaId={job.media_id} />
      )}

      {/* Copy Editor Handoff - shown for completed jobs */}
      {job.status === 'completed' && (
        <CopyEditorHandoff projectName={job.project_name} />
      )}

      {/* Truncation Warning Banner */}
      {job.status === 'paused' && job.error_message?.includes('TRUNCATION') && (
        <div role="alert" aria-live="assertive" className="bg-amber-900/20 border border-amber-500/30 rounded-lg p-4">
          <div className="flex items-start space-x-3">
            <span className="text-amber-400 text-xl flex-shrink-0 mt-0.5">&#9888;</span>
            <div className="flex-1">
              <h3 className="text-amber-300 font-medium mb-1">Transcript Truncation Detected</h3>
              <p className="text-sm text-amber-200/80 mb-3">
                The LLM stopped generating before reaching the end of the transcript.
                The formatter output covers significantly less content than the source file.
              </p>
              <pre className="text-xs text-amber-300/70 whitespace-pre-wrap mb-3 bg-amber-950/30 rounded p-2">
                {job.error_message}
              </pre>
              <button
                onClick={() => handleAction('retry')}
                className="px-4 py-2 bg-amber-600 hover:bg-amber-500 text-white rounded-md text-sm font-medium transition-colors"
                title="Retry this job"
              >
                Retry
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Error Message (non-truncation errors) */}
      {job.error_message && !job.error_message.includes('TRUNCATION') && (
        <div role="alert" aria-live="assertive" className="bg-red-900/20 border border-red-500/30 rounded-lg p-4">
          <h3 className="text-red-400 font-medium mb-2">Error</h3>
          <pre className="text-sm text-red-300 whitespace-pre-wrap">
            {job.error_message}
          </pre>
        </div>
      )}

      {/* Timestamps */}
      <div className="bg-gray-800 rounded-lg border border-gray-700 p-4">
        <h2 className="text-lg font-medium text-white mb-4">Timeline</h2>
        <div className="grid grid-cols-2 gap-4 text-sm">
          <div>
            <span className="text-gray-400">Queued:</span>
            <span className="ml-2 text-white" title={formatTimestamp(job.queued_at)}>
              {formatRelativeTime(job.queued_at)}
            </span>
          </div>
          {job.started_at && (
            <div>
              <span className="text-gray-400">Started:</span>
              <span className="ml-2 text-white" title={formatTimestamp(job.started_at)}>
                {formatRelativeTime(job.started_at)}
              </span>
            </div>
          )}
          {job.completed_at && (
            <div>
              <span className="text-gray-400">Completed:</span>
              <span className="ml-2 text-white" title={formatTimestamp(job.completed_at)}>
                {formatRelativeTime(job.completed_at)}
              </span>
            </div>
          )}
          {job.started_at && job.completed_at && (
            <div>
              <span className="text-gray-400">Duration:</span>
              <span className="ml-2 text-white">
                {formatDuration(job.started_at, job.completed_at)}
              </span>
            </div>
          )}
          {job.last_heartbeat && job.status === 'in_progress' && (
            <div>
              <span className="text-gray-400">Last heartbeat:</span>
              <span className="ml-2 text-white" title={formatTimestamp(job.last_heartbeat)}>
                {formatRelativeTime(job.last_heartbeat)}
              </span>
            </div>
          )}
          {(() => {
            const retriedPhases = (job.phases || []).filter((p) => p.retry_count > 0);
            const totalRetries = retriedPhases.reduce((sum, p) => sum + (p.retry_count || 0), 0);
            return (
              <div>
                <span className="text-gray-400">Retries:</span>
                <span className="ml-2 text-white">
                  {totalRetries === 0
                    ? 'None'
                    : `${totalRetries} (${retriedPhases.map((p) => p.name).join(', ')})`}
                </span>
              </div>
            );
          })()}
        </div>
      </div>

      {/* Phase Retry Modal */}
      {retryModal && (
        <div
          className="fixed inset-0 bg-black/80 flex items-center justify-center z-50 p-4"
          onClick={() => setRetryModal(null)}
        >
          <div
            className="bg-gray-900 rounded-lg border border-gray-700 w-full max-w-md"
            role="dialog"
            aria-modal="true"
            aria-labelledby="retry-modal-title"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700">
              <h3 id="retry-modal-title" className="text-base font-medium text-white">
                Retry: {retryModal.label}
              </h3>
              <button
                onClick={() => setRetryModal(null)}
                className="text-gray-400 hover:text-white text-2xl leading-none"
                aria-label="Close retry dialog"
              >
                &times;
              </button>
            </div>
            <div className="p-4 space-y-4">
              <div>
                <label htmlFor="retry-model" className="block text-sm text-gray-300 mb-1">
                  Model
                </label>
                <select
                  id="retry-model"
                  value={retryModel}
                  onChange={(e) => setRetryModel(e.target.value)}
                  className="w-full bg-gray-800 border border-gray-600 rounded px-3 py-2 pr-8 text-sm text-white focus:outline-none focus:border-blue-500"
                >
                  <option value="">Phase default</option>
                  {availableModels.map(m => (
                    <option key={m.id} value={m.id}>{m.name}</option>
                  ))}
                </select>
              </div>
              {(() => {
                const phaseName = OUTPUT_TO_PHASE[retryModal.outputKey] || retryModal.outputKey
                const flags = job?.validation_result?.phase_results?.[phaseName]?.flags || []
                if (flags.length === 0) return null
                return (
                  <div className="bg-red-900/20 border border-red-800/50 rounded p-3">
                    <div className="text-xs text-red-400 font-medium mb-1">Validation Issues:</div>
                    {flags.map((flag: string, i: number) => (
                      <div key={i} className="text-xs text-red-300 ml-2">• {flag}</div>
                    ))}
                  </div>
                )
              })()}
              <div>
                <label htmlFor="retry-feedback" className="block text-sm text-gray-300 mb-1">
                  Editorial feedback <span className="text-gray-500">(optional)</span>
                </label>
                <textarea
                  id="retry-feedback"
                  value={retryFeedback}
                  onChange={(e) => setRetryFeedback(e.target.value)}
                  placeholder="Optional: describe what to change..."
                  rows={4}
                  className="w-full bg-gray-800 border border-gray-600 rounded px-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500 resize-y"
                />
              </div>
              <div className="flex gap-3 justify-end pt-1">
                <button
                  onClick={() => setRetryModal(null)}
                  className="px-4 py-2 text-sm text-gray-300 hover:text-white transition-colors"
                >
                  Cancel
                </button>
                <button
                  onClick={submitRetryModal}
                  className="px-4 py-2 bg-orange-600 hover:bg-orange-500 text-white text-sm rounded transition-colors"
                >
                  Retry
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Output Viewer Modal */}
      {viewingOutput && (
        <div
          className="fixed inset-0 bg-black/80 flex items-center justify-center z-50 p-4"
          onClick={closeModal}
        >
          <div
            ref={modalRef}
            className="bg-gray-900 rounded-lg border border-gray-700 w-full max-w-4xl max-h-[90vh] flex flex-col"
            role="dialog"
            aria-modal="true"
            aria-labelledby="output-modal-title"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Modal Header */}
            <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700">
              <h3 id="output-modal-title" className="text-lg font-medium text-white">
                {viewingOutput.label}
              </h3>
              <button
                onClick={closeModal}
                className="text-gray-400 hover:text-white text-2xl leading-none"
                aria-label="Close output viewer"
              >
                &times;
              </button>
            </div>
            {/* Modal Content */}
            <div className="flex-1 overflow-auto p-4">
              {viewingOutput.isJson ? (
                <pre className="text-sm text-gray-300 whitespace-pre-wrap font-mono">
                  {viewingOutput.content}
                </pre>
              ) : (
                <ProseContainer content={viewingOutput.content} />
              )}
            </div>
          </div>
        </div>
      )}

      {/* Screengrab Slideout */}
      {showScreengrabs && job?.media_id && (
        <div className="fixed right-0 top-0 h-full w-1/3 min-w-[350px] bg-gray-900 border-l border-gray-700 z-40 shadow-xl">
          <ScreengrabSlideout
            mediaId={job.media_id}
            onClose={() => setShowScreengrabs(false)}
          />
        </div>
      )}
    </div>
  )
}

function CopyEditorHandoff({ projectName }: { projectName: string }) {
  const [copied, setCopied] = useState(false)
  const promptText = `I'd like to edit ${projectName}`

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(promptText)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // Fallback: text is selectable
    }
  }

  return (
    <div className="flex items-center justify-between py-3 px-4 bg-gray-800 rounded-lg border border-gray-700">
      <div className="flex items-center gap-3 min-w-0">
        <span className="text-sm text-gray-400">Copy editing prompt:</span>
        <code className="text-sm text-white select-all cursor-text truncate">
          {promptText}
        </code>
      </div>
      <button
        onClick={handleCopy}
        className="ml-4 flex-shrink-0 px-3 py-1 text-sm text-gray-300 hover:text-white bg-gray-700 hover:bg-gray-600 rounded transition-colors"
        aria-label="Copy prompt to clipboard"
      >
        {copied ? 'Copied' : 'Copy'}
      </button>
    </div>
  )
}
