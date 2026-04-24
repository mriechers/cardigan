import { useEffect, useState } from 'react'

interface ModelStats {
  model_name: string
  request_count: number
  total_cost: number
  total_tokens: number
  avg_latency_ms: number | null
  cost_percentage: number | null
}

interface ModelStatsResponse {
  available: boolean
  error: string | null
  models: ModelStats[]
  period_start: string | null
  period_end: string | null
  period_days: number
  total_cost: number
  total_requests: number
}

interface ModelStatsWidgetProps {
  className?: string
}

/**
 * Widget displaying real-time model usage statistics from Langfuse.
 * Shows which models OpenRouter actually selected (not just preset config).
 */
export default function ModelStatsWidget({ className = '' }: ModelStatsWidgetProps) {
  const [stats, setStats] = useState<ModelStatsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [days, setDays] = useState(7)

  const fetchStats = async () => {
    setLoading(true)
    try {
      const response = await fetch(`/api/langfuse/model-stats?days=${days}`)
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`)
      }
      const data = await response.json()
      setStats(data)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch stats')
      setStats(null)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchStats()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [days])

  // Format model name for display (remove provider prefix for cleaner look)
  const formatModelName = (name: string) => {
    if (!name) return 'Unknown'
    // Keep full name but truncate if too long
    if (name.length > 40) {
      return name.slice(0, 37) + '...'
    }
    return name
  }

  // Format cost with appropriate precision
  const formatCost = (cost: number) => {
    if (cost < 0.01) return `$${cost.toFixed(4)}`
    if (cost < 1) return `$${cost.toFixed(3)}`
    return `$${cost.toFixed(2)}`
  }

  // Get bar width as percentage for visual representation
  const getBarWidth = (model: ModelStats) => {
    if (!stats || stats.total_cost === 0) return 0
    return Math.max(2, (model.total_cost / stats.total_cost) * 100)
  }

  // Determine color based on model tier (heuristic based on name)
  const getModelColor = (name: string) => {
    const lowerName = name.toLowerCase()
    // Premium models
    if (lowerName.includes('opus') || lowerName.includes('gpt-4') || lowerName.includes('pro')) {
      return 'bg-pbs-300'
    }
    // Free tier
    if (lowerName.includes('free') || lowerName.includes(':free')) {
      return 'bg-pbs-500'
    }
    // Default/balanced
    return 'bg-pbs-500'
  }

  return (
    <div className={`bg-surface-800 rounded-lg border border-surface-700 p-4 ${className}`}>
      <div className="flex items-center justify-between mb-3">
        <div>
          <h3 className="text-sm font-medium text-surface-400 uppercase tracking-wide">
            Actual Model Usage
          </h3>
          <p className="text-xs text-surface-400 mt-0.5">
            From Langfuse analytics
          </p>
        </div>
        <div className="flex items-center space-x-2">
          <select
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className="bg-surface-700 text-surface-300 text-xs rounded px-2 py-1 border border-surface-600 focus:outline-none focus:ring-1 focus:ring-pbs-400"
            aria-label="Time period"
          >
            <option value={7}>Last 7 days</option>
            <option value={14}>Last 14 days</option>
            <option value={30}>Last 30 days</option>
          </select>
          <button
            onClick={fetchStats}
            disabled={loading}
            className="text-xs text-surface-400 hover:text-white disabled:opacity-50"
            aria-label="Refresh stats"
          >
            {loading ? '...' : 'Refresh'}
          </button>
        </div>
      </div>

      {/* Loading state */}
      {loading && !stats && (
        <div className="animate-pulse space-y-3">
          <div className="h-4 bg-surface-700 rounded w-3/4"></div>
          <div className="h-4 bg-surface-700 rounded w-1/2"></div>
          <div className="h-4 bg-surface-700 rounded w-2/3"></div>
        </div>
      )}

      {/* Error state */}
      {error && !loading && (
        <div className="text-center py-4">
          <p className="text-status-failed text-sm">{error}</p>
          <button
            onClick={fetchStats}
            className="mt-2 text-xs text-pbs-400 hover:text-pbs-300"
          >
            Try again
          </button>
        </div>
      )}

      {/* Langfuse not available */}
      {stats && !stats.available && (
        <div className="text-center py-4">
          <p className="text-status-pending text-sm">Langfuse not configured</p>
          <p className="text-xs text-surface-400 mt-1">{stats.error}</p>
        </div>
      )}

      {/* Stats display */}
      {stats && stats.available && (
        <>
          {/* Summary row */}
          <div className="flex justify-between text-sm mb-4 pb-3 border-b border-surface-700">
            <div>
              <span className="text-surface-400">Total Requests:</span>{' '}
              <span className="text-white font-medium">{stats.total_requests.toLocaleString()}</span>
            </div>
            <div>
              <span className="text-surface-400">Total Cost:</span>{' '}
              <span className="text-white font-medium">{formatCost(stats.total_cost)}</span>
            </div>
          </div>

          {/* Model list */}
          {stats.models.length === 0 ? (
            <p className="text-surface-400 text-sm text-center py-4">
              No model usage data for this period
            </p>
          ) : (
            <div className="space-y-3">
              {stats.models.map((model, idx) => (
                <div key={idx} className="relative">
                  {/* Background bar showing proportion */}
                  <div
                    className={`absolute inset-y-0 left-0 ${getModelColor(model.model_name)} opacity-20 rounded`}
                    style={{ width: `${getBarWidth(model)}%` }}
                  />

                  {/* Content */}
                  <div className="relative flex items-center justify-between py-2 px-2">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center space-x-2">
                        <span
                          className="text-sm text-white font-mono truncate"
                          title={model.model_name}
                        >
                          {formatModelName(model.model_name)}
                        </span>
                        {model.cost_percentage !== null && model.cost_percentage > 0 && (
                          <span className="text-xs text-surface-400">
                            {model.cost_percentage.toFixed(0)}%
                          </span>
                        )}
                      </div>
                      <div className="flex items-center space-x-3 text-xs text-surface-400 mt-0.5">
                        <span>{model.request_count} requests</span>
                        <span>{model.total_tokens.toLocaleString()} tokens</span>
                        {model.avg_latency_ms && (
                          <span>{Math.round(model.avg_latency_ms)}ms avg</span>
                        )}
                      </div>
                    </div>
                    <div className="text-right ml-3">
                      <span className="text-sm font-mono text-white">
                        {formatCost(model.total_cost)}
                      </span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Footer with link to Langfuse */}
          <div className="mt-4 pt-3 border-t border-surface-700 flex justify-between items-center">
            <span className="text-xs text-surface-600">
              Data from {stats.period_start ? new Date(stats.period_start).toLocaleDateString() : 'N/A'}
              {' - '}
              {stats.period_end ? new Date(stats.period_end).toLocaleDateString() : 'now'}
            </span>
            <a
              href="https://cloud.langfuse.com"
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-pbs-400 hover:text-pbs-300 flex items-center space-x-1"
            >
              <span>Open Langfuse</span>
              <span>&#8599;</span>
            </a>
          </div>
        </>
      )}
    </div>
  )
}
