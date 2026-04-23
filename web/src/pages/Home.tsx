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
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-white">Dashboard</h1>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <StatCard
          label="Pending"
          value={stats?.pending ?? 0}
          color="text-yellow-400"
        />
        <StatCard
          label="Processing"
          value={stats?.in_progress ?? 0}
          color="text-pbs-400"
        />
        <StatCard
          label="Completed"
          value={stats?.completed ?? 0}
          color="text-green-400"
        />
        <StatCard
          label="Failed"
          value={stats?.failed ?? 0}
          color="text-red-400"
        />
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
            <div className="px-4 py-8 text-center text-surface-300">
              No jobs in queue
            </div>
          ) : (
            recentJobs.map((job) => (
              <Link
                key={job.id}
                to={`/jobs/${job.id}`}
                className="block px-4 py-3 hover:bg-surface-800 transition-colors"
              >
                <div className="flex items-center justify-between">
                  <div>
                    <div className="text-white font-medium">
                      {job.project_name}
                    </div>
                    <div className="text-sm text-surface-400" title={formatTimestamp(job.queued_at + 'Z')}>
                      {formatRelativeTime(job.queued_at + 'Z')}
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
  )
}

function StatCard({
  label,
  value,
  color,
}: {
  label: string
  value: number
  color: string
}) {
  return (
    <div className="bg-surface-800 rounded-lg border border-surface-700 p-4">
      <div className="text-sm text-surface-300">{label}</div>
      <div className={`text-3xl font-bold ${color}`}>{value}</div>
    </div>
  )
}
