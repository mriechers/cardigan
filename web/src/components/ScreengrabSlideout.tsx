import { useState, useEffect, useCallback } from 'react'
import { useToast } from './ui/Toast'
import { formatRelativeTime } from '../utils/formatTime'

interface ScreengrabFile {
  id: number
  filename: string
  remote_url: string
  media_id: string | null
  status: string
  first_seen_at: string
  sst_record_id: string | null
  attached_at: string | null
}

interface ScreengrabListResponse {
  screengrabs: ScreengrabFile[]
  total_new: number
  total_attached: number
  total_no_match: number
  sst_existing_attachments: number | null
  sst_record_id: string | null
}

interface AttachResponse {
  success: boolean
  media_id: string
  filename: string
  sst_record_id: string | null
  error_message: string | null
  skipped_duplicate: boolean
}

interface ScreengrabSlideoutProps {
  mediaId: string
  onClose: () => void
}

export default function ScreengrabSlideout({ mediaId, onClose }: ScreengrabSlideoutProps) {
  const [screengrabs, setScreengrabs] = useState<ScreengrabFile[]>([])
  const [loading, setLoading] = useState(true)
  const [attaching, setAttaching] = useState<number | null>(null)
  const [batchAttaching, setBatchAttaching] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [existingAttachments, setExistingAttachments] = useState<number | null>(null)
  const [sstRecordId, setSstRecordId] = useState<string | null>(null)
  const { toast } = useToast()

  // Airtable URL for SST record
  const AIRTABLE_BASE_ID = 'appZ2HGwhiifQToB6'
  const AIRTABLE_SST_TABLE_ID = 'tblTKFOwTvK7xw1H5'

  const fetchScreengrabs = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const response = await fetch(`/api/ingest/screengrabs/for-media-id/${mediaId}`)
      if (!response.ok) {
        throw new Error('Failed to fetch screengrabs')
      }
      const data: ScreengrabListResponse = await response.json()
      setScreengrabs(data.screengrabs || [])
      setExistingAttachments(data.sst_existing_attachments)
      setSstRecordId(data.sst_record_id)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }, [mediaId])

  useEffect(() => {
    fetchScreengrabs()
  }, [fetchScreengrabs])

  const handleAttach = async (fileId: number) => {
    setAttaching(fileId)
    try {
      const response = await fetch(`/api/ingest/screengrabs/${fileId}/attach`, { method: 'POST' })
      if (!response.ok) {
        throw new Error('Failed to attach screengrab')
      }
      const data: AttachResponse = await response.json()

      if (data.success) {
        if (data.skipped_duplicate) {
          toast('Screengrab already attached (skipped duplicate)', 'info')
        } else {
          toast('Screengrab attached to Airtable successfully', 'success')
        }
        // Remove from list or update status
        setScreengrabs(screengrabs.filter(s => s.id !== fileId))
      } else {
        toast(data.error_message || 'Failed to attach screengrab', 'error')
      }
    } catch (err) {
      toast(err instanceof Error ? err.message : 'Failed to attach screengrab', 'error')
    } finally {
      setAttaching(null)
    }
  }

  const handleIgnore = async (fileId: number) => {
    try {
      const response = await fetch(`/api/ingest/screengrabs/${fileId}/ignore`, { method: 'POST' })
      if (!response.ok) {
        throw new Error('Failed to ignore screengrab')
      }

      toast('Screengrab ignored', 'success')
      setScreengrabs(screengrabs.filter(s => s.id !== fileId))
    } catch (err) {
      toast(err instanceof Error ? err.message : 'Failed to ignore screengrab', 'error')
    }
  }

  const handleAttachAll = async () => {
    if (screengrabs.length === 0) return

    setBatchAttaching(true)
    let successCount = 0
    let errorCount = 0

    for (const screengrab of screengrabs) {
      if (screengrab.status !== 'new' && screengrab.status !== 'no_match') continue

      try {
        const response = await fetch(`/api/ingest/screengrabs/${screengrab.id}/attach`, { method: 'POST' })
        if (response.ok) {
          const data: AttachResponse = await response.json()
          if (data.success) {
            successCount++
          } else {
            errorCount++
          }
        } else {
          errorCount++
        }
      } catch {
        errorCount++
      }
    }

    if (successCount > 0) {
      toast(`${successCount} screengrab${successCount !== 1 ? 's' : ''} attached successfully`, 'success')
    }
    if (errorCount > 0) {
      toast(`${errorCount} screengrab${errorCount !== 1 ? 's' : ''} failed to attach`, 'error')
    }

    fetchScreengrabs()
    setBatchAttaching(false)
  }

  const pendingCount = screengrabs.filter(s => s.status === 'new' || s.status === 'no_match').length

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-surface-700">
        <div>
          <h3 className="text-lg font-semibold text-white">Screengrabs</h3>
          <p className="text-sm text-surface-400">
            {pendingCount} available for {mediaId}
          </p>
        </div>
        <button
          onClick={onClose}
          className="text-surface-400 hover:text-white text-2xl leading-none px-2"
          aria-label="Close screengrab panel"
        >
          &times;
        </button>
      </div>

      {/* Attach All Button */}
      {pendingCount > 1 && (
        <div className="px-4 py-3 border-b border-surface-700">
          <button
            onClick={handleAttachAll}
            disabled={batchAttaching}
            className="w-full px-4 py-2 bg-purple-600 hover:bg-purple-500 disabled:opacity-50 text-white rounded-lg transition-colors font-medium"
          >
            {batchAttaching ? 'Attaching...' : `Attach All (${pendingCount})`}
          </button>
        </div>
      )}

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-4">
        {/* Existing Attachments Banner */}
        {existingAttachments !== null && existingAttachments > 0 && (
          <div className="mb-4 p-3 bg-pbs-900/20 border border-pbs-500/30 rounded-lg">
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-2">
                <svg className="w-5 h-5 text-pbs-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <p className="text-pbs-300 text-sm">
                  <span className="font-medium">{existingAttachments}</span> image{existingAttachments !== 1 ? 's' : ''} already attached
                </p>
              </div>
              {sstRecordId && (
                <a
                  href={`https://airtable.com/${AIRTABLE_BASE_ID}/${AIRTABLE_SST_TABLE_ID}/${sstRecordId}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-pbs-400 hover:text-pbs-300 text-sm flex items-center space-x-1"
                >
                  <span>View in Airtable</span>
                  <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                  </svg>
                </a>
              )}
            </div>
          </div>
        )}

        {/* Show Airtable link even if no existing attachments */}
        {existingAttachments === 0 && sstRecordId && (
          <div className="mb-4">
            <a
              href={`https://airtable.com/${AIRTABLE_BASE_ID}/${AIRTABLE_SST_TABLE_ID}/${sstRecordId}`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-surface-400 hover:text-surface-300 text-sm flex items-center space-x-1"
            >
              <span>View SST record in Airtable</span>
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
              </svg>
            </a>
          </div>
        )}

        {error && (
          <div className="mb-4 p-3 bg-red-900/20 border border-red-500/30 rounded-lg">
            <p className="text-red-400 text-sm">{error}</p>
          </div>
        )}

        {loading ? (
          <div className="py-8 text-center">
            <p className="text-surface-400 animate-pulse">Loading screengrabs...</p>
          </div>
        ) : screengrabs.length === 0 ? (
          <div className="py-8 text-center">
            <p className="text-surface-400">No screengrabs available for this project</p>
            <p className="text-sm text-surface-400 mt-1">
              Screengrabs may have already been attached or none were found on the ingest server.
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {screengrabs.map((screengrab) => (
              <div
                key={screengrab.id}
                className="bg-surface-800 rounded-lg border border-surface-700 overflow-hidden"
              >
                {/* Thumbnail */}
                <div className="aspect-video bg-surface-900 relative">
                  <img
                    src={screengrab.remote_url}
                    alt={screengrab.filename}
                    className="w-full h-full object-cover"
                    loading="lazy"
                    onError={(e) => {
                      (e.target as HTMLImageElement).src = 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text y=".9em" font-size="90">🖼</text></svg>'
                    }}
                  />

                  {/* Status Badge */}
                  {screengrab.status === 'attached' && (
                    <div className="absolute top-2 right-2 px-2 py-1 bg-green-600 text-white text-xs rounded">
                      Attached
                    </div>
                  )}
                </div>

                {/* Info */}
                <div className="p-3">
                  <p className="text-sm text-surface-300 truncate" title={screengrab.filename}>
                    {screengrab.filename}
                  </p>
                  <p className="text-xs text-surface-400 mt-1">
                    {formatRelativeTime(screengrab.first_seen_at + 'Z')}
                  </p>

                  {/* Actions */}
                  {(screengrab.status === 'new' || screengrab.status === 'no_match') && (
                    <div className="flex items-center space-x-2 mt-3">
                      <button
                        onClick={() => handleAttach(screengrab.id)}
                        disabled={attaching === screengrab.id}
                        className="flex-1 px-3 py-1.5 text-sm bg-purple-600 hover:bg-purple-500 disabled:opacity-50 text-white rounded transition-colors"
                      >
                        {attaching === screengrab.id ? 'Attaching...' : 'Attach'}
                      </button>
                      <button
                        onClick={() => handleIgnore(screengrab.id)}
                        className="px-3 py-1.5 text-sm bg-surface-700 hover:bg-surface-600 text-surface-300 rounded transition-colors"
                      >
                        Ignore
                      </button>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
