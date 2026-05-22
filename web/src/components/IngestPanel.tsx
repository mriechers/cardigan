import { useState, useEffect, useCallback } from 'react'
import { useToast } from './ui/Toast'
import { formatRelativeTime } from '../utils/formatTime'

interface SSTRecordInfo {
  id: string
  title: string | null
  project: string | null
}

interface AvailableFile {
  id: number
  filename: string
  media_id: string | null
  file_type: string
  remote_url: string
  first_seen_at: string
  status: string
  sst_record: SSTRecordInfo | null
}

interface AvailableFilesResponse {
  files: AvailableFile[]
  total_new: number
  last_scan_at: string | null
}

interface ScanResponse {
  success: boolean
  qc_passed_checked: number
  new_files_found: number
  scan_duration_ms: number
  error_message: string | null
}

interface QueueResponse {
  success: boolean
  file_id: number
  media_id: string | null
  job_id: number | null
  error: string | null
}

export default function IngestPanel() {
  const [files, setFiles] = useState<AvailableFile[]>([])
  const [totalNew, setTotalNew] = useState(0)
  const [lastScanAt, setLastScanAt] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [scanning, setScanning] = useState(false)
  const [selectedFileIds, setSelectedFileIds] = useState<Set<number>>(new Set())
  const [error, setError] = useState<string | null>(null)
  const { toast } = useToast()

  const fetchAvailableFiles = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const response = await fetch('/api/ingest/available?status=new&file_type=transcript')
      if (!response.ok) {
        throw new Error('Failed to fetch available files')
      }
      const data: AvailableFilesResponse = await response.json()
      setFiles(data.files || [])
      setTotalNew(data.total_new)
      setLastScanAt(data.last_scan_at)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchAvailableFiles()
  }, [fetchAvailableFiles])

  const handleScan = async () => {
    setScanning(true)
    setError(null)
    try {
      const response = await fetch('/api/ingest/scan', { method: 'POST' })
      if (!response.ok) {
        throw new Error('Failed to scan for new files')
      }
      const data: ScanResponse = await response.json()

      if (data.success) {
        toast(
          `Scan complete: ${data.new_files_found} new file${data.new_files_found !== 1 ? 's' : ''} found`,
          'success'
        )
        fetchAvailableFiles()
      } else {
        setError(data.error_message || 'Scan failed')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Scan failed')
    } finally {
      setScanning(false)
    }
  }

  const handleQueueFile = async (fileId: number) => {
    try {
      const response = await fetch(`/api/ingest/transcripts/${fileId}/queue`, { method: 'POST' })
      if (!response.ok) {
        throw new Error('Failed to queue file')
      }
      const data: QueueResponse = await response.json()

      if (data.success) {
        toast(`File queued for processing (Job #${data.job_id})`, 'success')
        // Remove from list
        setFiles(files.filter(f => f.id !== fileId))
        setTotalNew(prev => prev - 1)
        setSelectedFileIds(prev => {
          const updated = new Set(prev)
          updated.delete(fileId)
          return updated
        })
      } else {
        toast(data.error || 'Failed to queue file', 'error')
      }
    } catch (err) {
      toast(err instanceof Error ? err.message : 'Failed to queue file', 'error')
    }
  }

  const handleQueueSelected = async () => {
    if (selectedFileIds.size === 0) return

    try {
      const response = await fetch('/api/ingest/transcripts/queue', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file_ids: Array.from(selectedFileIds) })
      })

      if (!response.ok) {
        throw new Error('Failed to queue files')
      }

      const results = await response.json()
      const successCount = results.filter((r: QueueResponse) => r.success).length
      const failCount = results.length - successCount

      if (successCount > 0) {
        toast(`${successCount} file${successCount !== 1 ? 's' : ''} queued successfully`, 'success')
      }
      if (failCount > 0) {
        toast(`${failCount} file${failCount !== 1 ? 's' : ''} failed to queue`, 'error')
      }

      // Refresh the list
      fetchAvailableFiles()
      setSelectedFileIds(new Set())
    } catch (err) {
      toast(err instanceof Error ? err.message : 'Failed to queue files', 'error')
    }
  }

  const handleIgnoreFile = async (fileId: number) => {
    try {
      const response = await fetch(`/api/ingest/transcripts/${fileId}/ignore`, { method: 'POST' })
      if (!response.ok) {
        throw new Error('Failed to ignore file')
      }

      toast('File ignored', 'success')
      // Remove from list
      setFiles(files.filter(f => f.id !== fileId))
      setTotalNew(prev => prev - 1)
      setSelectedFileIds(prev => {
        const updated = new Set(prev)
        updated.delete(fileId)
        return updated
      })
    } catch (err) {
      toast(err instanceof Error ? err.message : 'Failed to ignore file', 'error')
    }
  }

  const toggleFileSelection = (fileId: number) => {
    setSelectedFileIds(prev => {
      const updated = new Set(prev)
      if (updated.has(fileId)) {
        updated.delete(fileId)
      } else {
        updated.add(fileId)
      }
      return updated
    })
  }

  const toggleSelectAll = () => {
    if (selectedFileIds.size === files.length) {
      setSelectedFileIds(new Set())
    } else {
      setSelectedFileIds(new Set(files.map(f => f.id)))
    }
  }

  return (
    <div className="bg-surface-800 rounded-lg border border-surface-700 p-6 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-white">Ready to Queue</h2>
          <p className="text-sm text-surface-400">
            {totalNew} transcript{totalNew !== 1 ? 's' : ''} ready for processing
          </p>
        </div>
        <div className="flex items-center space-x-3">
          {lastScanAt && (
            <span className="text-sm text-surface-400" title={new Date(lastScanAt + 'Z').toLocaleString()}>
              Last scan: {formatRelativeTime(lastScanAt + 'Z')}
            </span>
          )}
          <button
            onClick={handleScan}
            disabled={scanning}
            className="px-3 py-1.5 text-sm bg-surface-700 hover:bg-surface-600 disabled:opacity-50 text-white rounded-md transition-colors"
            aria-label="Scan for new transcript files"
          >
            {scanning ? 'Checking...' : 'Check Now'}
          </button>
        </div>
      </div>

      {/* Error Message */}
      {error && (
        <div role="alert" aria-live="assertive" className="bg-red-900/20 border border-red-500/30 rounded-lg p-4">
          <p className="text-red-400">{error}</p>
        </div>
      )}

      {/* Bulk Actions */}
      {selectedFileIds.size > 0 && (
        <div className="bg-pbs-900/20 border border-pbs-500/30 rounded-lg p-4">
          <div className="flex items-center justify-between">
            <span className="text-pbs-400 text-sm">
              {selectedFileIds.size} file{selectedFileIds.size !== 1 ? 's' : ''} selected
            </span>
            <button
              onClick={handleQueueSelected}
              className="px-3 py-1.5 text-sm bg-green-600 hover:bg-green-500 text-white rounded-md transition-colors"
              aria-label={`Queue ${selectedFileIds.size} selected files`}
            >
              Queue Selected
            </button>
          </div>
        </div>
      )}

      {/* File List */}
      {loading ? (
        <div className="py-8 text-center">
          <p className="text-surface-400 animate-pulse">Loading available files...</p>
        </div>
      ) : files.length === 0 ? (
        <div className="py-8 text-center">
          <p className="text-surface-400">No new transcript files available</p>
          <p className="text-sm text-surface-400 mt-1">Click "Check Now" to scan for new files</p>
        </div>
      ) : (
        <div className="space-y-2">
          {/* Select All */}
          {files.length > 1 && (
            <div className="flex items-center px-3 py-2 bg-surface-900 rounded-lg">
              <label className="flex items-center space-x-3 cursor-pointer">
                <input
                  type="checkbox"
                  checked={selectedFileIds.size === files.length}
                  onChange={toggleSelectAll}
                  className="w-4 h-4 rounded border-surface-600 bg-surface-700 text-pbs-500 focus:ring-pbs-400 focus:ring-offset-surface-800"
                  aria-label="Select all files"
                />
                <span className="text-sm text-surface-400 font-medium">Select All</span>
              </label>
            </div>
          )}

          {/* File Items */}
          {files.map((file) => (
            <div
              key={file.id}
              className="flex items-center justify-between p-3 bg-surface-900 rounded-lg hover:bg-surface-850 transition-colors"
            >
              <div className="flex items-center space-x-3 flex-1 min-w-0">
                <label className="flex items-center cursor-pointer">
                  <input
                    type="checkbox"
                    checked={selectedFileIds.has(file.id)}
                    onChange={() => toggleFileSelection(file.id)}
                    className="w-4 h-4 rounded border-surface-600 bg-surface-700 text-pbs-500 focus:ring-pbs-400 focus:ring-offset-surface-800"
                    aria-label={`Select ${file.media_id || file.filename}`}
                  />
                </label>

                <div className="flex-1 min-w-0">
                  <div className="flex items-center space-x-2">
                    {file.media_id ? (
                      <span className="font-medium text-white font-mono">{file.media_id}</span>
                    ) : (
                      <span className="font-medium text-surface-400">{file.filename}</span>
                    )}
                  </div>

                  {file.sst_record && (
                    <div className="text-sm text-surface-400 mt-0.5">
                      {file.sst_record.title && (
                        <span className="mr-2">{file.sst_record.title}</span>
                      )}
                      {file.sst_record.project && (
                        <span className="text-surface-400">• {file.sst_record.project}</span>
                      )}
                    </div>
                  )}

                  {!file.sst_record && file.media_id && (
                    <div className="text-xs text-yellow-500 mt-0.5">
                      No SST record found
                    </div>
                  )}
                </div>
              </div>

              <div className="flex items-center space-x-2 ml-4">
                <button
                  onClick={() => handleQueueFile(file.id)}
                  className="px-3 py-1 text-sm bg-green-600 hover:bg-green-500 text-white rounded transition-colors"
                  aria-label={`Queue ${file.media_id || file.filename} for processing`}
                >
                  Queue
                </button>
                <button
                  onClick={() => handleIgnoreFile(file.id)}
                  className="px-3 py-1 text-sm bg-surface-700 hover:bg-surface-600 text-surface-300 rounded transition-colors"
                  aria-label={`Ignore ${file.media_id || file.filename}`}
                >
                  Ignore
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
