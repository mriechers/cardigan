import { useState, useEffect, useCallback } from 'react'
import { useToast } from './ui/Toast'

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

interface ScreengrabsBoxProps {
  mediaId: string
}

// Airtable URL constants
const AIRTABLE_BASE_ID = 'appZ2HGwhiifQToB6'
const AIRTABLE_SST_PAGE_ID = 'pagmlseGxLHMXLLBX'

function buildAirtableSstUrl(recordId: string): string {
  const detail = btoa(JSON.stringify({
    pageId: AIRTABLE_SST_PAGE_ID,
    rowId: recordId,
    showComments: false,
    queryOriginHint: null,
  }))
  return `https://airtable.com/${AIRTABLE_BASE_ID}/pagCh7J2dYzqPC3bH?detail=${detail}`
}

export default function ScreengrabsBox({ mediaId }: ScreengrabsBoxProps) {
  const [screengrabs, setScreengrabs] = useState<ScreengrabFile[]>([])
  const [loading, setLoading] = useState(true)
  const [attaching, setAttaching] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [existingAttachments, setExistingAttachments] = useState<number | null>(null)
  const [sstRecordId, setSstRecordId] = useState<string | null>(null)
  const { toast } = useToast()

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
        setScreengrabs(screengrabs.filter(s => s.id !== fileId))
        // Increment existing attachments count
        if (existingAttachments !== null) {
          setExistingAttachments(existingAttachments + 1)
        }
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

  const pendingCount = screengrabs.filter(s => s.status === 'new' || s.status === 'no_match').length

  // Don't render if no screengrabs and no existing attachments
  if (!loading && screengrabs.length === 0 && (existingAttachments === null || existingAttachments === 0)) {
    return null
  }

  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700">
        <div className="flex items-center space-x-2">
          <svg className="w-5 h-5 text-purple-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
          </svg>
          <h3 className="text-sm font-medium text-white">Screen Grabs</h3>
          {pendingCount > 0 && (
            <span className="px-2 py-0.5 text-xs bg-purple-600 text-white rounded-full">
              {pendingCount} available
            </span>
          )}
        </div>
        {sstRecordId && (
          <a
            href={buildAirtableSstUrl(sstRecordId)}
            target="_blank"
            rel="noopener noreferrer"
            className="text-gray-400 hover:text-gray-300 text-xs flex items-center space-x-1"
          >
            <span>View in Airtable</span>
            <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
            </svg>
          </a>
        )}
      </div>

      {/* Content */}
      <div className="p-4">
        {/* Existing attachments info */}
        {existingAttachments !== null && existingAttachments > 0 && (
          <div className="mb-3 text-sm text-gray-400">
            {existingAttachments} image{existingAttachments !== 1 ? 's' : ''} already attached in Airtable
          </div>
        )}

        {error && (
          <div className="mb-3 p-2 bg-red-900/20 border border-red-500/30 rounded text-red-400 text-sm">
            {error}
          </div>
        )}

        {loading ? (
          <div className="py-4 text-center">
            <p className="text-gray-400 text-sm animate-pulse">Loading...</p>
          </div>
        ) : screengrabs.length === 0 ? (
          <div className="py-2 text-center">
            <p className="text-gray-500 text-sm">All screengrabs have been processed</p>
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-3">
            {screengrabs.map((screengrab) => (
              <div
                key={screengrab.id}
                className="bg-gray-900 rounded-lg overflow-hidden border border-gray-700"
              >
                {/* Thumbnail */}
                <div className="aspect-video bg-gray-950 relative">
                  <a
                    href={screengrab.remote_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="block w-full h-full cursor-pointer"
                    aria-label={`View full size: ${screengrab.filename}`}
                  >
                    <img
                      src={screengrab.remote_url}
                      alt={screengrab.filename}
                      className="w-full h-full object-cover"
                      loading="lazy"
                      crossOrigin="anonymous"
                      onError={(e) => {
                        const img = e.target as HTMLImageElement
                        // Retry without crossOrigin if CORS fails
                        if (img.crossOrigin) {
                          img.crossOrigin = ''
                          img.src = screengrab.remote_url
                        } else {
                          img.src = 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text y=".9em" font-size="90">%F0%9F%96%BC</text></svg>'
                        }
                      }}
                    />
                  </a>
                </div>

                {/* Info & Actions */}
                <div className="p-2">
                  <p className="text-xs text-gray-400 truncate mb-2" title={screengrab.filename}>
                    {screengrab.filename}
                  </p>

                  {(screengrab.status === 'new' || screengrab.status === 'no_match') && (
                    <div className="flex items-center space-x-1">
                      <button
                        onClick={() => handleAttach(screengrab.id)}
                        disabled={attaching === screengrab.id}
                        className="flex-1 px-2 py-1 text-xs bg-purple-600 hover:bg-purple-500 disabled:opacity-50 text-white rounded transition-colors"
                      >
                        {attaching === screengrab.id ? '...' : 'Attach'}
                      </button>
                      <button
                        onClick={() => handleIgnore(screengrab.id)}
                        className="px-2 py-1 text-xs bg-gray-700 hover:bg-gray-600 text-gray-300 rounded transition-colors"
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
