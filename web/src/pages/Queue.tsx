import { useEffect, useState, useCallback } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import ConfirmDialog from '../components/ui/ConfirmDialog'
import { useToast } from '../components/ui/Toast'
import { SkeletonQueue } from '../components/ui/Skeleton'
import Button from '../components/ui/Button'
import { useDebounce } from '../hooks/useDebounce'
import { useJobsWebSocket } from '../hooks/useWebSocket'
import { formatRelativeTime, formatTimestamp } from '../utils/formatTime'
import { getStatusBadgeColor } from '../utils/statusColors'
import TranscriptUploader from '../components/TranscriptUploader'

interface Job {
  id: number
  project_name: string
  transcript_file: string
  status: string
  priority: number
  queued_at: string
  current_phase: string | null
}

interface PaginatedResponse {
  jobs: Job[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

interface QueueStats {
  pending: number
  in_progress: number
  completed: number
  failed: number
  cancelled: number
  paused: number
  total: number
}

const formatStatus = (status: string) => {
  const labels: Record<string, string> = {
    all: 'All jobs',
    pending: 'Pending',
    in_progress: 'Processing',
    completed: 'Completed',
    failed: 'Failed',
    cancelled: 'Cancelled',
  }
  return labels[status] || status
}

export default function Queue() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [jobs, setJobs] = useState<Job[]>([])
  const [filter, setFilter] = useState<string>('all')
  const [loading, setLoading] = useState(true)
  const [stats, setStats] = useState<QueueStats | null>(null)
  const [showUploader, setShowUploader] = useState(false)

  // Pagination and search state
  const [page, setPage] = useState(1)
  const [totalPages, setTotalPages] = useState(1)
  const [total, setTotal] = useState(0)
  const [searchInput, setSearchInput] = useState(searchParams.get('search') || '')
  const debouncedSearch = useDebounce(searchInput, 300)
  const PAGE_SIZE = 50

  // Dialog state
  const [confirmDialog, setConfirmDialog] = useState<{
    isOpen: boolean
    title: string
    message: string
    onConfirm: () => void
    variant?: 'danger' | 'warning' | 'info'
    confirmText?: string
  }>({
    isOpen: false,
    title: '',
    message: '',
    onConfirm: () => {},
  })

  const { toast } = useToast()

  // WebSocket connection for real-time updates
  const { isConnected } = useJobsWebSocket({
    onJobUpdate: (job, eventType) => {
      // Update jobs list when we receive updates
      setJobs((currentJobs) => {
        // If job is in current filter, update it
        const existingIndex = currentJobs.findIndex((j) => j.id === job.id)

        if (existingIndex !== -1) {
          // Update existing job
          const newJobs = [...currentJobs]
          newJobs[existingIndex] = job as Job
          return newJobs
        } else if (filter === 'all' || filter === job.status) {
          // Add new job if it matches current filter
          return [job as Job, ...currentJobs]
        }

        return currentJobs
      })

      // Show toast for certain events
      if (eventType === 'job_completed') {
        toast(`Job #${job.id} completed successfully`, 'success')
      } else if (eventType === 'job_failed') {
        toast(`Job #${job.id} failed`, 'error')
      }
    },
    onStatsUpdate: (newStats) => {
      setStats(newStats)
    },
  })

  const handlePrioritize = async (jobId: number) => {
    try {
      // Find max priority and set this job higher
      const maxPriority = Math.max(...jobs.map(j => j.priority), 0)
      await fetch(`/api/jobs/${jobId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ priority: maxPriority + 10 })
      })
      fetchJobs() // Refresh
    } catch (err) {
      console.error('Failed to prioritize job:', err)
    }
  }

  const handleCancel = (jobId: number) => {
    setConfirmDialog({
      isOpen: true,
      title: 'Cancel this job?',
      message: 'This will stop processing and remove the job from the queue.',
      variant: 'danger',
      confirmText: 'Cancel job',
      onConfirm: async () => {
        try {
          const response = await fetch(`/api/jobs/${jobId}/cancel`, { method: 'POST' })
          if (response.ok) {
            toast('Job cancelled successfully', 'success')
            fetchJobs()
          } else {
            toast('Failed to cancel job', 'error')
          }
        } catch (err) {
          console.error('Failed to cancel job:', err)
          toast('Failed to cancel job', 'error')
        }
        setConfirmDialog({ ...confirmDialog, isOpen: false })
      },
    })
  }

  const handleClearJobs = (statuses: string[]) => {
    const statusLabels = statuses.join(' and ')

    setConfirmDialog({
      isOpen: true,
      title: `Delete ${statusLabels} jobs?`,
      message: `This permanently removes all ${statusLabels} jobs and their outputs.`,
      variant: 'danger',
      confirmText: 'Delete jobs',
      onConfirm: async () => {
        try {
          const params = new URLSearchParams()
          statuses.forEach(s => params.append('statuses', s))

          const response = await fetch(`/api/queue/bulk?${params}`, { method: 'DELETE' })
          if (response.ok) {
            const result = await response.json()
            toast(result.message, 'success')
            fetchJobs()
          } else {
            toast('Failed to delete jobs', 'error')
          }
        } catch (err) {
          console.error('Failed to clear jobs:', err)
          toast('Failed to delete jobs', 'error')
        }
        setConfirmDialog({ ...confirmDialog, isOpen: false })
      },
    })
  }

  const fetchStats = useCallback(async () => {
    try {
      const response = await fetch('/api/queue/stats')
      if (response.ok) {
        const data: QueueStats = await response.json()
        setStats(data)
      }
    } catch (err) {
      console.error('Failed to fetch stats:', err)
    }
  }, [])

  const fetchJobs = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams({
        page: page.toString(),
        page_size: PAGE_SIZE.toString(),
        sort: 'newest',
      })

      // Only add status filter if not 'all'
      if (filter !== 'all') {
        params.set('status', filter)
      }

      if (debouncedSearch) {
        params.set('search', debouncedSearch)
      }

      const response = await fetch(`/api/queue/?${params}`)
      if (response.ok) {
        const data: PaginatedResponse = await response.json()
        setJobs(data.jobs || [])
        setTotal(data.total)
        setTotalPages(data.total_pages)
      }
    } catch (err) {
      console.error('Failed to fetch jobs:', err)
    } finally {
      setLoading(false)
    }
  }, [filter, page, debouncedSearch])

  useEffect(() => {
    fetchJobs()
    fetchStats()

    // Fallback polling if WebSocket is not connected
    // Poll less frequently since WebSocket handles real-time updates
    const pollInterval = isConnected ? 30000 : 5000 // 30s if WS connected, 5s if not

    const interval = setInterval(() => {
      fetchStats() // Always refresh stats periodically
      if (!isConnected) {
        fetchJobs() // Only poll jobs if WebSocket is down
      }
    }, pollInterval)

    return () => clearInterval(interval)
  }, [fetchJobs, fetchStats, isConnected])

  // Update URL when search changes
  useEffect(() => {
    const params = new URLSearchParams(searchParams)
    if (debouncedSearch) {
      params.set('search', debouncedSearch)
    } else {
      params.delete('search')
    }
    setSearchParams(params, { replace: true })
  }, [debouncedSearch, setSearchParams, searchParams])

  // Reset to first page when search changes
  useEffect(() => {
    if (debouncedSearch !== searchParams.get('search')) {
      setPage(1)
    }
  }, [debouncedSearch, searchParams])

  const clearSearch = () => {
    setSearchInput('')
    setPage(1)
  }

  const handleFilterChange = (newFilter: string) => {
    setFilter(newFilter)
    setPage(1) // Reset to first page when filter changes
  }

  const handleUploadComplete = () => {
    // Refresh jobs and stats after upload
    fetchJobs()
    fetchStats()
    setShowUploader(false)
  }

  return (
    <div className="space-y-6">
      {/* Header with Title, Search, and Count */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-display font-bold text-white">Job Queue</h1>
          <span className="text-surface-400 text-sm">
            {total} job{total !== 1 ? 's' : ''}
            {filter !== 'all' && ` (${filter.replace('_', ' ')})`}
            {debouncedSearch && ` matching "${debouncedSearch}"`}
          </span>
        </div>

        {/* Upload, Clear Button and Search */}
        <div className="flex items-center gap-4">
          {/* Upload Button */}
          <Button
            variant="primary"
            size="sm"
            onClick={() => setShowUploader(!showUploader)}
            title="Upload transcript files"
          >
            {showUploader ? 'Hide Upload' : '+ Upload'}
          </Button>

          {/* Delete Old Jobs Button */}
          <Button
            variant="ghost"
            size="sm"
            onClick={() => handleClearJobs(['failed', 'cancelled'])}
            title="Delete all failed and cancelled jobs"
          >
            Delete old jobs
          </Button>

          {/* Instant Search */}
          <div className="relative">
            <label htmlFor="queue-search" className="sr-only">Search jobs by filename</label>
            <input
              id="queue-search"
              type="text"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="Search by filename..."
              className="bg-surface-800 border border-surface-700 rounded-lg px-4 py-2 text-white placeholder-surface-400 focus:outline-none focus:border-pbs-500 w-64 pr-8"
              aria-describedby="queue-search-desc"
            />
            {searchInput && (
              <button
                type="button"
                onClick={clearSearch}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-surface-400 hover:text-white"
                aria-label="Clear search"
              >
                ✕
              </button>
            )}
            <span id="queue-search-desc" className="sr-only">
              Search filters automatically as you type. Results appear after you stop typing for 300ms.
            </span>
          </div>
        </div>
      </div>

      {/* Upload Component */}
      {showUploader && (
        <TranscriptUploader onUploadComplete={handleUploadComplete} />
      )}

      {/* Filter Tabs */}
      <div className="flex items-center space-x-1 bg-surface-800 rounded-lg p-1 w-fit">
        {['all', 'pending', 'in_progress', 'completed', 'failed', 'cancelled'].map((status) => {
          const count = stats
            ? status === 'all'
              ? stats.total
              : stats[status as keyof QueueStats]
            : null

          return (
            <button
              key={status}
              onClick={() => handleFilterChange(status)}
              className={`px-3 py-1.5 text-sm rounded-md transition-colors flex items-center space-x-1.5 ${
                filter === status
                  ? 'bg-surface-700 text-white'
                  : 'text-surface-400 hover:text-white'
              }`}
            >
              <span>{formatStatus(status)}</span>
              {count !== null && (
                <span
                  className={`px-1.5 py-0.5 text-xs rounded-full ${
                    filter === status
                      ? 'bg-surface-600 text-surface-200'
                      : 'bg-surface-700 text-surface-400'
                  }`}
                >
                  {count}
                </span>
              )}
            </button>
          )
        })}
      </div>

      {/* Jobs Table */}
      {loading ? (
        <SkeletonQueue />
      ) : (
        <div className="bg-surface-800 rounded-lg border border-surface-700 overflow-hidden">
        {jobs.length === 0 ? (
          <div className="px-4 py-8 text-center text-surface-300">
            No jobs found
          </div>
        ) : (
          <table className="w-full">
            <thead className="bg-surface-850 border-b border-surface-700">
              <tr className="text-left text-sm text-surface-300">
                <th className="px-4 py-3 font-medium">ID</th>
                <th className="px-4 py-3 font-medium">Transcript</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium">Phase</th>
                <th className="px-4 py-3 font-medium">Created</th>
                <th className="px-4 py-3 font-medium">Priority</th>
                <th className="px-4 py-3 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-surface-700">
              {jobs.map((job) => (
                <tr
                  key={job.id}
                  className="hover:bg-surface-800 transition-colors"
                >
                  <td className="px-4 py-3">
                    <Link
                      to={`/jobs/${job.id}`}
                      className="text-pbs-400 hover:text-pbs-300"
                    >
                      #{job.id}
                    </Link>
                  </td>
                  <td className="px-4 py-3 font-medium text-white">
                    {job.project_name}
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium border ${getStatusBadgeColor(
                        job.status
                      )}`}
                    >
                      {formatStatus(job.status)}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-surface-300 text-sm">
                    {job.current_phase || '-'}
                  </td>
                  <td
                    className="px-4 py-3 text-surface-300 text-sm"
                    title={formatTimestamp(job.queued_at + 'Z')}
                  >
                    {formatRelativeTime(job.queued_at + 'Z')}
                  </td>
                  <td className="px-4 py-3 text-surface-300 text-sm">
                    {job.priority}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center space-x-2">
                      {job.status === 'pending' && (
                        <>
                          <Button
                            variant="secondary"
                            size="sm"
                            onClick={() => handlePrioritize(job.id)}
                            title="Move to top of queue"
                          >
                            ↑ Prioritize
                          </Button>
                          <Button
                            variant="danger"
                            size="sm"
                            onClick={() => handleCancel(job.id)}
                            title="Cancel job"
                          >
                            Cancel
                          </Button>
                        </>
                      )}
                      {job.status === 'in_progress' && (
                        <span className="text-xs text-surface-300">Processing...</span>
                      )}
                      {['completed', 'failed', 'cancelled'].includes(job.status) && (
                        <Link
                          to={`/jobs/${job.id}`}
                          className="text-xs text-surface-300 hover:text-white"
                        >
                          View details
                        </Link>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        </div>
      )}

      {/* Pagination Controls */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center space-x-4 py-4">
          <Button
            variant="secondary"
            onClick={() => setPage(p => Math.max(1, p - 1))}
            disabled={page === 1}
          >
            ← Previous
          </Button>
          <span className="text-surface-400">
            Page {page} of {totalPages}
          </span>
          <Button
            variant="secondary"
            onClick={() => setPage(p => Math.min(totalPages, p + 1))}
            disabled={page === totalPages}
          >
            Next →
          </Button>
        </div>
      )}

      {/* Confirm Dialog */}
      <ConfirmDialog
        isOpen={confirmDialog.isOpen}
        onConfirm={confirmDialog.onConfirm}
        onCancel={() => setConfirmDialog({ ...confirmDialog, isOpen: false })}
        title={confirmDialog.title}
        message={confirmDialog.message}
        variant={confirmDialog.variant}
        confirmText={confirmDialog.confirmText || 'Confirm'}
        cancelText="Cancel"
      />
    </div>
  )
}
