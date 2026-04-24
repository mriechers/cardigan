import { useEffect, useState, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { SkeletonDashboard } from '../components/ui/Skeleton'
import { useJobsWebSocket } from '../hooks/useWebSocket'
import { formatRelativeTime, formatTimestamp } from '../utils/formatTime'
import { getStatusTextColor } from '../utils/statusColors'

interface QueueStats {
  pending: number
  in_progress: number
  completed: number
  failed: number
}

interface RecentJob {
  id: number
  project_name: string
  transcript_file: string
  status: string
  queued_at: string
  priority: number
}

export default function Home() {
  const [stats, setStats] = useState<QueueStats | null>(null)
  const [recentJobs, setRecentJobs] = useState<RecentJob[]>([])
  const [loading, setLoading] = useState(true)

  // WebSocket connection for real-time updates
  const { isConnected } = useJobsWebSocket({
    onJobUpdate: (job) => {
      // Update recent jobs list when we receive updates
      setRecentJobs((currentJobs) => {
        const existingIndex = currentJobs.findIndex((j) => j.id === job.id)

        if (existingIndex !== -1) {
          // Update existing job
          const newJobs = [...currentJobs]
          newJobs[existingIndex] = job as RecentJob
          return newJobs
        } else {
          // Add new job to top of list, keep only 5 most recent
          return [job as RecentJob, ...currentJobs].slice(0, 5)
        }
      })
    },
    onStatsUpdate: (newStats) => {
      setStats(newStats)
    },
  })

  const fetchData = useCallback(async () => {
    try {
      const [statsRes, jobsRes] = await Promise.all([
        fetch('/api/queue/stats'),
        fetch('/api/queue/?page=1&page_size=5&sort=newest'),
      ])

      if (statsRes.ok) {
        setStats(await statsRes.json())
      }
      if (jobsRes.ok) {
        const data = await jobsRes.json()
        // API returns paginated response with jobs array
        setRecentJobs(data.jobs || [])
      }
    } catch (err) {
      console.error('Failed to fetch dashboard data:', err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchData()

    // Fallback polling - poll less frequently when WebSocket is connected
    const pollInterval = isConnected ? 30000 : 10000

    const interval = setInterval(fetchData, pollInterval)
    return () => clearInterval(interval)
  }, [fetchData, isConnected])

  if (loading) {
    return <SkeletonDashboard />
  }

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-display font-bold text-white">Dashboard</h1>

      <div className="space-y-4">
        {/* Queue Summary — single compact row, not 4 cards */}
        <div className="flex items-center gap-6 px-4 py-3 bg-surface-850 rounded-lg border border-surface-700">
          <span className="text-sm font-medium text-surface-300">Queue</span>
          <div className="flex items-center gap-4 text-sm">
            <span>
              <span className="font-mono font-medium text-status-pending">{stats?.pending ?? 0}</span>
              <span className="text-surface-400 ml-1">pending</span>
            </span>
            <span>
              <span className="font-mono font-medium text-status-processing">{stats?.in_progress ?? 0}</span>
              <span className="text-surface-400 ml-1">processing</span>
            </span>
            <span>
              <span className="font-mono font-medium text-status-completed">{stats?.completed ?? 0}</span>
              <span className="text-surface-400 ml-1">done</span>
            </span>
            {(stats?.failed ?? 0) > 0 && (
              <span>
                <span className="font-mono font-medium text-status-failed">{stats?.failed}</span>
                <span className="text-surface-400 ml-1">failed</span>
              </span>
            )}
          </div>
        </div>

        {/* Recent Jobs */}
        <div className="bg-surface-800 rounded-lg border border-surface-700">
        <div className="px-4 py-3 border-b border-surface-700 flex items-center justify-between">
          <h2 className="text-lg font-medium text-white">Recent Jobs</h2>
          <Link
            to="/queue"
            className="text-sm text-pbs-400 hover:text-pbs-300"
          >
            View all
          </Link>
        </div>
        <div className="divide-y divide-surface-700">
          {recentJobs.length === 0 ? (
            <div className="px-6 py-12 text-center">
              <p className="text-surface-300 font-medium">No jobs in the queue</p>
              <p className="text-surface-400 text-sm mt-1">
                Upload transcripts from the <Link to="/ready" className="text-pbs-400 hover:text-pbs-300">Ready for Work</Link> page to get started.
              </p>
            </div>
          ) : (
            recentJobs.map((job) => (
              <Link
                key={job.id}
                to={`/jobs/${job.id}`}
                className="block px-4 py-3 hover:bg-surface-800 transition-colors"
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <div className={`w-2 h-2 rounded-full flex-shrink-0 ${
                      job.status === 'completed' ? 'bg-status-completed' :
                      job.status === 'in_progress' ? 'bg-status-processing animate-pulse' :
                      job.status === 'failed' ? 'bg-status-failed' :
                      job.status === 'pending' ? 'bg-status-pending' :
                      'bg-surface-500'
                    }`} />
                    <div>
                      <div className="text-white font-medium">{job.project_name}</div>
                      <div className="text-sm text-surface-400" title={formatTimestamp(job.queued_at + 'Z')}>
                        {formatRelativeTime(job.queued_at + 'Z')}
                      </div>
                    </div>
                  </div>
                  <span className={`text-sm font-medium ${getStatusTextColor(job.status)}`}>
                    {job.status}
                  </span>
                </div>
              </Link>
            ))
          )}
        </div>
        </div>
      </div>
    </div>
  )
}
