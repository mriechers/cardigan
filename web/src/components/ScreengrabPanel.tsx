import { useEffect, useState, useCallback } from 'react'

interface ScreengrabFile {
  id: number
  filename: string
  remote_url: string
  media_id: string | null
  status: string  // 'new', 'attached', 'no_match', 'ignored'
  first_seen_at: string
  sst_record_id: string | null
  attached_at: string | null
}

interface ScreengrabListResponse {
  screengrabs: ScreengrabFile[]
  total_new: number
  total_attached: number
  total_no_match: number
}

interface AttachResponse {
  success: boolean
  media_id: string
  filename: string
  sst_record_id: string | null
  attachments_before: number
  attachments_after: number
  error_message: string | null
  skipped_duplicate: boolean
}

interface BatchAttachResponse {
  total_processed: number
  attached: number
  skipped_no_match: number
  skipped_duplicate: number
  errors: string[]
}

type FilterTab = 'pending' | 'no_match' | 'all'

export default function ScreengrabPanel() {
  const [screengrabs, setScreengrabs] = useState<ScreengrabFile[]>([])
  const [activeFilter, setActiveFilter] = useState<FilterTab>('pending')
  const [loading, setLoading] = useState(true)
  const [attaching, setAttaching] = useState<number | null>(null)
  const [batchAttaching, setBatchAttaching] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [counts, setCounts] = useState({ new: 0, attached: 0, no_match: 0 })

  const fetchScreengrabs = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const status = activeFilter === 'pending' ? 'new' : activeFilter === 'no_match' ? 'no_match' : undefined
      const params = new URLSearchParams()
      if (status) params.set('status', status)

      const response = await fetch(`/api/ingest/screengrabs?${params}`)
      if (!response.ok) {
        throw new Error('Failed to fetch screengrabs')
      }
      const data: ScreengrabListResponse = await response.json()
      setScreengrabs(data.screengrabs || [])
      setCounts({
        new: data.total_new || 0,
        attached: data.total_attached || 0,
        no_match: data.total_no_match || 0
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }, [activeFilter])

  useEffect(() => {
    fetchScreengrabs()
  }, [fetchScreengrabs])

  const handleAttach = async (fileId: number) => {
    setAttaching(fileId)
    setError(null)
    setSuccess(null)

    try {
      const response = await fetch(`/api/ingest/screengrabs/${fileId}/attach`, {
        method: 'POST'
      })
      const data: AttachResponse = await response.json()

      if (response.ok && data.success) {
        if (data.skipped_duplicate) {
          setSuccess(`Skipped: ${data.filename} already attached to ${data.media_id}`)
        } else {
          setSuccess(`Attached ${data.filename} to ${data.media_id}${data.sst_record_id ? ' (SST matched)' : ''}`)
        }
        await fetchScreengrabs()
      } else {
        setError(data.error_message || 'Failed to attach screengrab')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setAttaching(null)
      setTimeout(() => setSuccess(null), 5000)
    }
  }

  const handleAttachAll = async () => {
    setBatchAttaching(true)
    setError(null)
    setSuccess(null)

    try {
      const response = await fetch('/api/ingest/screengrabs/attach-all', {
        method: 'POST'
      })
      const data: BatchAttachResponse = await response.json()

      if (response.ok) {
        const messages = []
        if (data.attached > 0) {
          messages.push(`Attached: ${data.attached}`)
        }
        if (data.skipped_duplicate > 0) {
          messages.push(`Skipped (duplicate): ${data.skipped_duplicate}`)
        }
        if (data.skipped_no_match > 0) {
          messages.push(`Skipped (no match): ${data.skipped_no_match}`)
        }
        if (data.errors.length > 0) {
          messages.push(`Errors: ${data.errors.length}`)
        }

        setSuccess(messages.join(' | '))
        await fetchScreengrabs()
      } else {
        setError('Failed to process batch attachment')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setBatchAttaching(false)
      setTimeout(() => setSuccess(null), 5000)
    }
  }

  const handleIgnore = async (fileId: number) => {
    setAttaching(fileId)
    setError(null)
    setSuccess(null)

    try {
      const response = await fetch(`/api/ingest/screengrabs/${fileId}/ignore`, {
        method: 'POST'
      })

      if (response.ok) {
        setSuccess('Screengrab marked as ignored')
        await fetchScreengrabs()
      } else {
        setError('Failed to ignore screengrab')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setAttaching(null)
      setTimeout(() => setSuccess(null), 3000)
    }
  }

  const pendingCount = counts.new
  const filteredScreengrabs = screengrabs

  if (loading && screengrabs.length === 0) {
    return (
      <div className="bg-surface-800 rounded-lg border border-surface-700 p-6">
        <div className="text-surface-300 animate-pulse">Loading screengrabs...</div>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-white">Screengrabs</h2>
        {pendingCount > 0 && (
          <button
            onClick={handleAttachAll}
            disabled={batchAttaching || attaching !== null}
            className="px-4 py-2 bg-pbs-500 hover:bg-pbs-400 disabled:opacity-50 text-white rounded-lg text-sm transition-colors"
            aria-label={`Attach all ${pendingCount} pending screengrabs`}
          >
            {batchAttaching ? 'Attaching...' : `Attach All (${pendingCount})`}
          </button>
        )}
      </div>

      {/* Status Messages */}
      {error && (
        <div role="alert" aria-live="assertive" className="bg-status-failed/15 border border-status-failed/30 rounded-lg p-4">
          <p className="text-status-failed text-sm">{error}</p>
        </div>
      )}
      {success && (
        <div role="status" aria-live="polite" className="bg-status-completed/15 border border-status-completed/30 rounded-lg p-4">
          <p className="text-status-completed text-sm">{success}</p>
        </div>
      )}

      {/* Filter Tabs */}
      <div className="border-b border-surface-700">
        <nav className="flex space-x-1" role="tablist" aria-label="Screengrab filters">
          <button
            role="tab"
            aria-selected={activeFilter === 'pending'}
            aria-controls="panel-pending"
            onClick={() => setActiveFilter('pending')}
            className={`px-4 py-2 text-sm font-medium rounded-t-lg transition-colors ${
              activeFilter === 'pending'
                ? 'bg-surface-700 text-white'
                : 'text-surface-400 hover:text-white hover:bg-surface-800/50'
            }`}
          >
            Pending {counts.new > 0 && <span className="ml-1">({counts.new})</span>}
          </button>
          <button
            role="tab"
            aria-selected={activeFilter === 'no_match'}
            aria-controls="panel-no_match"
            onClick={() => setActiveFilter('no_match')}
            className={`px-4 py-2 text-sm font-medium rounded-t-lg transition-colors ${
              activeFilter === 'no_match'
                ? 'bg-surface-700 text-white'
                : 'text-surface-400 hover:text-white hover:bg-surface-800/50'
            }`}
          >
            No Match {counts.no_match > 0 && <span className="ml-1">({counts.no_match})</span>}
          </button>
          <button
            role="tab"
            aria-selected={activeFilter === 'all'}
            aria-controls="panel-all"
            onClick={() => setActiveFilter('all')}
            className={`px-4 py-2 text-sm font-medium rounded-t-lg transition-colors ${
              activeFilter === 'all'
                ? 'bg-surface-700 text-white'
                : 'text-surface-400 hover:text-white hover:bg-surface-800/50'
            }`}
          >
            All
          </button>
        </nav>
      </div>

      {/* No Match Explanation */}
      {activeFilter === 'no_match' && counts.no_match > 0 && (
        <div className="bg-status-pending/15 border border-status-pending/30 rounded-lg p-4">
          <div className="flex items-start space-x-3">
            <span className="text-status-pending text-xl">⚠️</span>
            <div>
              <h3 className="text-sm font-medium text-status-pending">No SST Match Found</h3>
              <p className="text-xs text-surface-300 mt-1">
                These screengrabs could not be automatically matched to a Media ID in the SST.
                The filename pattern does not match expected formats (e.g., &quot;2WLI...&quot; or &quot;NOLA...&quot;).
                You may need to manually attach these files or verify the filename.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Screengrabs List */}
      <div
        role="tabpanel"
        id={`panel-${activeFilter}`}
        aria-labelledby={activeFilter}
        className="bg-surface-800 rounded-lg border border-surface-700 p-6"
      >
        {filteredScreengrabs.length === 0 ? (
          <div className="text-center py-8">
            <p className="text-surface-300">No {activeFilter === 'pending' ? 'pending' : activeFilter === 'no_match' ? 'unmatched' : ''} screengrabs found.</p>
            <p className="text-surface-400 text-sm mt-2">
              {activeFilter === 'pending' && 'New screengrabs will appear here when detected.'}
              {activeFilter === 'no_match' && 'Files that cannot be matched to Media IDs will appear here.'}
              {activeFilter === 'all' && 'No screengrabs have been processed yet.'}
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {filteredScreengrabs.map((screengrab) => (
              <div
                key={screengrab.id}
                className="bg-surface-900 rounded-lg p-3 flex items-center gap-4"
              >
                {/* Thumbnail */}
                <div className="flex-shrink-0">
                  <img
                    src={screengrab.remote_url}
                    alt={`Thumbnail for ${screengrab.filename}`}
                    className="w-16 h-12 object-cover rounded border border-surface-700"
                    loading="lazy"
                  />
                </div>

                {/* Info */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-start gap-2">
                    <div className="flex-1 min-w-0">
                      <div className="text-white text-sm font-medium truncate" title={screengrab.filename}>
                        {screengrab.filename}
                      </div>
                      {screengrab.media_id && (
                        <div className="text-surface-400 text-xs font-mono">
                          Media ID: {screengrab.media_id}
                        </div>
                      )}
                      {screengrab.sst_record_id && (
                        <div className="text-status-completed text-xs">
                          SST Match Found
                        </div>
                      )}
                    </div>

                    {/* Status Badge */}
                    <div>
                      {screengrab.status === 'attached' && (
                        <span className="inline-flex items-center px-2 py-1 rounded text-xs bg-status-completed/15 text-status-completed border border-status-completed/30">
                          Attached
                        </span>
                      )}
                      {screengrab.status === 'no_match' && (
                        <span className="inline-flex items-center px-2 py-1 rounded text-xs bg-status-pending/15 text-status-pending border border-status-pending/30">
                          No Match
                        </span>
                      )}
                      {screengrab.status === 'ignored' && (
                        <span className="inline-flex items-center px-2 py-1 rounded text-xs bg-surface-700/30 text-surface-400 border border-surface-600/30">
                          Ignored
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Timestamp */}
                  {screengrab.attached_at && (
                    <div className="text-surface-400 text-xs mt-1">
                      Attached: {new Date(screengrab.attached_at).toLocaleString()}
                    </div>
                  )}
                  {!screengrab.attached_at && screengrab.first_seen_at && (
                    <div className="text-surface-400 text-xs mt-1">
                      Detected: {new Date(screengrab.first_seen_at).toLocaleString()}
                    </div>
                  )}
                </div>

                {/* Actions */}
                {screengrab.status === 'new' && (
                  <div className="flex-shrink-0 flex items-center gap-2">
                    <button
                      onClick={() => handleAttach(screengrab.id)}
                      disabled={attaching === screengrab.id || batchAttaching}
                      className="px-3 py-1 bg-pbs-500 hover:bg-pbs-400 disabled:opacity-50 text-white text-sm rounded transition-colors"
                      aria-label={`Attach ${screengrab.filename}`}
                    >
                      {attaching === screengrab.id ? 'Attaching...' : 'Attach'}
                    </button>
                    <button
                      onClick={() => handleIgnore(screengrab.id)}
                      disabled={attaching === screengrab.id || batchAttaching}
                      className="px-3 py-1 bg-surface-700 hover:bg-surface-600 disabled:opacity-50 text-surface-300 text-sm rounded transition-colors"
                      aria-label={`Ignore ${screengrab.filename}`}
                    >
                      Ignore
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Summary Footer */}
      <div className="flex items-center justify-between text-sm text-surface-400 px-2">
        <div>
          Total: {counts.new + counts.attached + counts.no_match} screengrabs
        </div>
        <div className="flex gap-4">
          <span>Attached: {counts.attached}</span>
          <span>Pending: {counts.new}</span>
          <span>No Match: {counts.no_match}</span>
        </div>
      </div>
    </div>
  )
}
