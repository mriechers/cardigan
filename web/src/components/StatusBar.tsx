import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

interface HealthStatus {
  status: string
  queue?: {
    pending: number
    in_progress: number
  }
}

export default function StatusBar() {
  const [health, setHealth] = useState<HealthStatus | null>(null)
  const [error, setError] = useState<string | null>(null)

  const fetchHealth = async () => {
    try {
      const response = await fetch('/api/system/health')
      if (!response.ok) throw new Error('API unavailable')
      const data = await response.json()
      setHealth(data)
      setError(null)
    } catch {
      setError('API offline')
      setHealth(null)
    }
  }

  useEffect(() => {
    fetchHealth()
    const interval = setInterval(fetchHealth, 10000)
    return () => clearInterval(interval)
  }, [])

  const queueTotal = health?.queue
    ? health.queue.pending + health.queue.in_progress
    : 0

  return (
    <div className="bg-gray-950 border-b border-gray-800 px-4 py-1.5">
      <div className="max-w-7xl mx-auto flex items-center justify-between text-xs">
        {/* Left: Connection Status */}
        <Link
          to="/settings"
          className="flex items-center space-x-2 hover:bg-gray-800 px-2 py-1 rounded transition-colors"
          title="View system settings"
          role="status"
          aria-live="polite"
        >
          <div
            className={`w-1.5 h-1.5 rounded-full ${
              error ? 'bg-red-500 animate-pulse' : 'bg-green-500'
            }`}
          />
          <span className={error ? 'text-red-400' : 'text-gray-400'}>
            {error ? 'Offline' : 'Connected'}
          </span>
        </Link>

        {/* Right: Queue Count */}
        {health?.queue && queueTotal > 0 && (
          <Link
            to="/queue"
            className="flex items-center space-x-1.5 text-gray-400 hover:bg-gray-800 px-2 py-1 rounded transition-colors"
            title={`${health.queue.pending} pending, ${health.queue.in_progress} processing`}
          >
            <span className="text-yellow-400 font-medium">{queueTotal}</span>
            <span>in queue</span>
            {health.queue.in_progress > 0 && (
              <span className="text-blue-400 animate-pulse">●</span>
            )}
          </Link>
        )}
      </div>
    </div>
  )
}
