import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { isRemotePreview } from '../utils/preview'

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

  // In the remote preview the Settings page is hidden, so the status pill is a
  // plain (non-navigating) indicator rather than a deep-link into Settings.
  const remotePreview = isRemotePreview()

  const statusIndicator = (
    <>
      <div
        className={`w-1.5 h-1.5 rounded-full ${
          error ? 'bg-status-failed animate-pulse' : 'bg-status-completed'
        }`}
      />
      <span className={error ? 'text-status-failed' : 'text-surface-300'}>
        {error ? 'Offline' : 'Connected'}
      </span>
    </>
  )

  return (
    <div className="bg-surface-950 border-b border-surface-800 px-4 py-1.5">
      <div className="max-w-7xl mx-auto flex items-center justify-between text-xs">
        {/* Left: Connection Status */}
        {remotePreview ? (
          <div
            className="flex items-center space-x-2 px-2 py-1"
            role="status"
            aria-live="polite"
          >
            {statusIndicator}
          </div>
        ) : (
          <Link
            to="/settings"
            className="flex items-center space-x-2 hover:bg-surface-800 px-2 py-1 rounded transition-colors"
            title="View system settings"
            role="status"
            aria-live="polite"
          >
            {statusIndicator}
          </Link>
        )}

        {/* Right: Queue Count */}
        {health?.queue && queueTotal > 0 && (
          <Link
            to="/queue"
            className="flex items-center space-x-1.5 text-surface-400 hover:bg-surface-800 px-2 py-1 rounded transition-colors"
            title={`${health.queue.pending} pending, ${health.queue.in_progress} processing`}
          >
            <span className="text-status-pending font-medium">{queueTotal}</span>
            <span>in queue</span>
            {health.queue.in_progress > 0 && (
              <span className="text-status-processing animate-pulse">●</span>
            )}
          </Link>
        )}
      </div>
    </div>
  )
}
