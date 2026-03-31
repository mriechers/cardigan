import { useState, useEffect, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useToast } from '../components/ui/Toast'
import { formatRelativeTime, formatTimestamp } from '../utils/formatTime'

interface AvailableFile {
  id: number
  filename: string
  media_id: string | null
  file_type: string
  remote_url: string
  first_seen_at: string
  remote_modified_at: string | null  // Server modification time
  status: string
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

const DATE_RANGE_OPTIONS = [
  { value: 7, label: 'Last 7 days' },
  { value: 14, label: 'Last 14 days' },
  { value: 30, label: 'Last 30 days' },
  { value: 60, label: 'Last 60 days' },
  { value: 90, label: 'Last 90 days' },
]

export default function ReadyForWork() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [files, setFiles] = useState<AvailableFile[]>([])
  const [lastScanAt, setLastScanAt] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [scanning, setScanning] = useState(false)
  const [selectedFileIds, setSelectedFileIds] = useState<Set<number>>(new Set())
  const [error, setError] = useState<string | null>(null)
  const { toast } = useToast()

  // Filter state from URL params
  const search = searchParams.get('search') || ''
  const days = parseInt(searchParams.get('days') || '30', 10)

  // Debounced search input
  const [searchInput, setSearchInput] = useState(search)

  // Update URL params when filters change
  const updateFilters = useCallback((newSearch: string, newDays: number) => {
    const params = new URLSearchParams()
    if (newSearch) params.set('search', newSearch)
    if (newDays !== 30) params.set('days', newDays.toString())
    setSearchParams(params)
  }, [setSearchParams])

  // Debounce search input
  useEffect(() => {
    const timer = setTimeout(() => {
      if (searchInput !== search) {
        updateFilters(searchInput, days)
      }
    }, 300)
    return () => clearTimeout(timer)
  }, [searchInput, search, days, updateFilters])

  const fetchAvailableFiles = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams({
        status: 'new',
        file_type: 'transcript',
        days: days.toString(),
        exclude_with_jobs: 'true',
      })
      if (search) {
        params.set('search', search)
      }

      const response = await fetch(`/api/ingest/available?${params}`)
      if (!response.ok) {
        throw new Error('Failed to fetch available files')
      }
      const data: AvailableFilesResponse = await response.json()
      setFiles(data.files || [])
      setLastScanAt(data.last_scan_at)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }, [search, days])

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
        setFiles(files.filter(f => f.id !== fileId))
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

      const data = await response.json()
      const successCount = data.queued ?? 0
      const failCount = data.failed ?? 0

      if (successCount > 0) {
        toast(`${successCount} file${successCount !== 1 ? 's' : ''} queued successfully`, 'success')
      }
      if (failCount > 0) {
        toast(`${failCount} file${failCount !== 1 ? 's' : ''} failed to queue`, 'error')
      }

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
      setFiles(files.filter(f => f.id !== fileId))
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

  const clearFilters = () => {
    setSearchInput('')
    setSearchParams(new URLSearchParams())
  }

  const hasActiveFilters = search || days !== 30

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      {/* Page Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Ready for Work</h1>
          <p className="text-gray-400 mt-1">
            Transcripts from the ingest server ready for processing
          </p>
        </div>
        <div className="flex items-center space-x-3">
          {lastScanAt && (
            <span className="text-sm text-gray-400" title={new Date(lastScanAt + 'Z').toLocaleString()}>
              Last scan: {formatRelativeTime(lastScanAt + 'Z')}
            </span>
          )}
          <button
            onClick={handleScan}
            disabled={scanning}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white rounded-lg transition-colors font-medium"
          >
            {scanning ? 'Checking...' : 'Check for New Files'}
          </button>
        </div>
      </div>

      {/* Filter Bar */}
      <div className="bg-gray-800 rounded-lg border border-gray-700 p-4">
        <div className="flex items-center gap-4">
          {/* Search Input */}
          <div className="flex-1 max-w-md">
            <label htmlFor="search" className="sr-only">Search</label>
            <div className="relative">
              <input
                id="search"
                type="text"
                placeholder="Search by filename or Media ID..."
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                className="w-full px-4 py-2 pl-10 bg-gray-900 border border-gray-600 rounded-lg text-white placeholder-gray-400 focus:outline-none focus:border-blue-500"
              />
              <svg
                className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
            </div>
          </div>

          {/* Date Range Dropdown */}
          <div>
            <label htmlFor="days" className="sr-only">Date Range</label>
            <select
              id="days"
              value={days}
              onChange={(e) => updateFilters(search, parseInt(e.target.value, 10))}
              className="px-4 py-2 bg-gray-900 border border-gray-600 rounded-lg text-white focus:outline-none focus:border-blue-500"
            >
              {DATE_RANGE_OPTIONS.map(opt => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          </div>

          {/* Clear Filters */}
          {hasActiveFilters && (
            <button
              onClick={clearFilters}
              className="px-3 py-2 text-gray-400 hover:text-white transition-colors"
            >
              Clear filters
            </button>
          )}
        </div>

        {/* Active Filters Summary */}
        {hasActiveFilters && (
          <div className="mt-3 flex items-center gap-2 text-sm">
            <span className="text-gray-400">Showing:</span>
            {search && (
              <span className="px-2 py-0.5 bg-blue-900/50 text-blue-300 rounded">
                "{search}"
              </span>
            )}
            <span className="px-2 py-0.5 bg-gray-700 text-gray-300 rounded">
              {DATE_RANGE_OPTIONS.find(o => o.value === days)?.label}
            </span>
          </div>
        )}
      </div>

      {/* Error Message */}
      {error && (
        <div role="alert" className="bg-red-900/20 border border-red-500/30 rounded-lg p-4">
          <p className="text-red-400">{error}</p>
        </div>
      )}

      {/* Bulk Actions */}
      {selectedFileIds.size > 0 && (
        <div className="bg-blue-900/20 border border-blue-500/30 rounded-lg p-4">
          <div className="flex items-center justify-between">
            <span className="text-blue-400">
              {selectedFileIds.size} file{selectedFileIds.size !== 1 ? 's' : ''} selected
            </span>
            <div className="flex items-center space-x-3">
              <button
                onClick={() => setSelectedFileIds(new Set())}
                className="px-3 py-1.5 text-sm text-gray-400 hover:text-white transition-colors"
              >
                Clear selection
              </button>
              <button
                onClick={handleQueueSelected}
                className="px-4 py-1.5 text-sm bg-green-600 hover:bg-green-500 text-white rounded-md transition-colors font-medium"
              >
                Queue Selected
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Results Card */}
      <div className="bg-gray-800 rounded-lg border border-gray-700">
        {/* Results Header */}
        <div className="px-6 py-4 border-b border-gray-700">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-white">
              {loading ? 'Loading...' : `${files.length} transcript${files.length !== 1 ? 's' : ''}`}
            </h2>
            {files.length > 1 && (
              <label className="flex items-center space-x-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={selectedFileIds.size === files.length && files.length > 0}
                  onChange={toggleSelectAll}
                  className="w-4 h-4 rounded border-gray-600 bg-gray-700 text-blue-600 focus:ring-blue-500"
                />
                <span className="text-sm text-gray-400">Select all</span>
              </label>
            )}
          </div>
        </div>

        {/* File List */}
        <div className="divide-y divide-gray-700">
          {loading ? (
            <div className="py-12 text-center">
              <p className="text-gray-400 animate-pulse">Loading available files...</p>
            </div>
          ) : files.length === 0 ? (
            <div className="py-12 text-center">
              <p className="text-gray-400">No transcript files match your filters</p>
              <p className="text-sm text-gray-500 mt-1">
                {hasActiveFilters
                  ? 'Try adjusting your search or date range'
                  : 'Click "Check for New Files" to scan for new transcripts'}
              </p>
            </div>
          ) : (
            files.map((file) => (
              <div
                key={file.id}
                className="flex items-center justify-between px-6 py-4 hover:bg-gray-750 transition-colors"
              >
                <div className="flex items-center space-x-4 flex-1 min-w-0">
                  <input
                    type="checkbox"
                    checked={selectedFileIds.has(file.id)}
                    onChange={() => toggleFileSelection(file.id)}
                    className="w-4 h-4 rounded border-gray-600 bg-gray-700 text-blue-600 focus:ring-blue-500"
                  />

                  <div className="flex-1 min-w-0">
                    <div className="flex items-center space-x-2">
                      {file.media_id ? (
                        <span className="font-medium text-white font-mono">{file.media_id}</span>
                      ) : (
                        <span className="font-medium text-gray-300 truncate">{file.filename}</span>
                      )}
                    </div>
                    <div className="text-sm text-gray-400 mt-0.5">
                      {formatTimestamp(
                        file.remote_modified_at || file.first_seen_at
                      )}
                      <span className="text-gray-500 ml-2">
                        ({formatRelativeTime(
                          file.remote_modified_at || file.first_seen_at
                        )})
                      </span>
                    </div>
                  </div>
                </div>

                <div className="flex items-center space-x-2 ml-4">
                  <button
                    onClick={() => handleQueueFile(file.id)}
                    className="px-4 py-1.5 text-sm bg-green-600 hover:bg-green-500 text-white rounded-md transition-colors font-medium"
                  >
                    Queue
                  </button>
                  <button
                    onClick={() => handleIgnoreFile(file.id)}
                    className="px-3 py-1.5 text-sm bg-gray-700 hover:bg-gray-600 text-gray-300 rounded-md transition-colors"
                  >
                    Ignore
                  </button>
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  )
}
