import { useEffect, useState } from 'react'

interface PhaseModelStats {
  model: string
  completions: number
  failures: number
  total_cost: number
  total_tokens: number
  success_rate: number
}

interface PhaseStats {
  phase: string
  total_completions: number
  total_failures: number
  total_cost: number
  total_tokens: number
  success_rate: number
  models: PhaseModelStats[]
}

interface PhaseStatsResponse {
  phases: PhaseStats[]
  period_start: string
  period_end: string
  period_days: number
  total_cost: number
  total_completions: number
  total_failures: number
}

interface PhaseStatsWidgetProps {
  className?: string
}

// Phase display info
const PHASE_INFO: Record<string, { name: string; icon: string }> = {
  analyst: { name: 'Analyst', icon: '🔍' },
  formatter: { name: 'Formatter', icon: '📝' },
  seo: { name: 'SEO', icon: '🎯' },
  validator: { name: 'Validator', icon: '✅' },
  copy_editor: { name: 'Copy Editor', icon: '✏️' },
  timestamp: { name: 'Timestamps', icon: '⏱️' },
}

/**
 * Unified agent performance widget.
 * Shows each agent's success rate and model breakdown.
 */
export default function PhaseStatsWidget({ className = '' }: PhaseStatsWidgetProps) {
  const [stats, setStats] = useState<PhaseStatsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [days, setDays] = useState(30)
  const [expandedPhase, setExpandedPhase] = useState<string | null>(null)

  const fetchStats = async () => {
    setLoading(true)
    try {
      const response = await fetch(`/api/langfuse/phase-stats?days=${days}`)
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

  const getSuccessColor = (rate: number) => {
    if (rate >= 99) return 'text-green-400'
    if (rate >= 95) return 'text-yellow-400'
    if (rate >= 90) return 'text-orange-400'
    return 'text-red-400'
  }

  const getBarColor = (rate: number) => {
    if (rate >= 99) return 'bg-green-500'
    if (rate >= 95) return 'bg-yellow-500'
    if (rate >= 90) return 'bg-orange-500'
    return 'bg-red-500'
  }

  const formatCost = (cost: number) => {
    if (cost < 0.01) return `$${cost.toFixed(4)}`
    if (cost < 1) return `$${cost.toFixed(3)}`
    return `$${cost.toFixed(2)}`
  }

  const getPhaseInfo = (phase: string) => {
    return PHASE_INFO[phase] || { name: phase, icon: '🤖' }
  }

  return (
    <div className={`bg-surface-800 rounded-lg border border-surface-700 p-4 ${className}`}>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="text-sm font-medium text-surface-400 uppercase tracking-wide">
            Agent Performance
          </h3>
          <p className="text-xs text-surface-400 mt-0.5">
            Model usage &amp; success rates by phase
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
            <option value={90}>Last 90 days</option>
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
          <div className="h-12 bg-surface-700 rounded"></div>
          <div className="h-12 bg-surface-700 rounded"></div>
          <div className="h-12 bg-surface-700 rounded"></div>
        </div>
      )}

      {/* Error state */}
      {error && !loading && (
        <div className="text-center py-4">
          <p className="text-red-400 text-sm">{error}</p>
          <button
            onClick={fetchStats}
            className="mt-2 text-xs text-pbs-400 hover:text-pbs-300"
          >
            Try again
          </button>
        </div>
      )}

      {/* Stats display */}
      {stats && !error && (
        <>
          {/* Summary bar */}
          <div className="flex justify-between text-sm mb-4 pb-3 border-b border-surface-700">
            <div>
              <span className="text-surface-400">Total:</span>{' '}
              <span className="text-white font-medium">
                {stats.total_completions.toLocaleString()} jobs
              </span>
              {stats.total_failures > 0 && (
                <span className="text-red-400 text-xs ml-2">
                  ({stats.total_failures} failed)
                </span>
              )}
            </div>
            <div>
              <span className="text-surface-400">Cost:</span>{' '}
              <span className="text-white font-medium">{formatCost(stats.total_cost)}</span>
            </div>
          </div>

          {/* Phase list */}
          {stats.phases.length === 0 ? (
            <p className="text-surface-400 text-sm text-center py-4">
              No phase data for this period
            </p>
          ) : (
            <div className="space-y-2">
              {stats.phases.map((phase) => {
                const info = getPhaseInfo(phase.phase)
                const isExpanded = expandedPhase === phase.phase

                return (
                  <div key={phase.phase} className="bg-surface-900 rounded-lg overflow-hidden">
                    {/* Main row - clickable */}
                    <button
                      onClick={() => setExpandedPhase(isExpanded ? null : phase.phase)}
                      className="w-full p-3 flex items-center justify-between hover:bg-surface-800/50 transition-colors"
                    >
                      <div className="flex items-center space-x-3">
                        <span className="text-lg">{info.icon}</span>
                        <div className="text-left">
                          <div className="flex items-center space-x-2">
                            <span className="text-white font-medium">{info.name}</span>
                            <span className="text-xs text-surface-400">
                              {phase.total_completions} runs
                            </span>
                          </div>
                          {/* Progress bar */}
                          <div className="flex items-center space-x-2 mt-1">
                            <div className="w-32 h-1.5 bg-surface-700 rounded-full overflow-hidden">
                              <div
                                className={`h-full ${getBarColor(phase.success_rate)} transition-all`}
                                style={{ width: `${phase.success_rate}%` }}
                              />
                            </div>
                            <span className={`text-xs font-mono ${getSuccessColor(phase.success_rate)}`}>
                              {phase.success_rate}%
                            </span>
                          </div>
                        </div>
                      </div>

                      <div className="flex items-center space-x-4 text-right">
                        {/* Cost */}
                        <div>
                          <div className="text-xs text-surface-400">Cost</div>
                          <div className="text-sm font-mono text-white">
                            {formatCost(phase.total_cost)}
                          </div>
                        </div>
                        {/* Expand indicator */}
                        <span className={`text-surface-400 transition-transform ${isExpanded ? 'rotate-180' : ''}`}>
                          ▼
                        </span>
                      </div>
                    </button>

                    {/* Expanded details */}
                    {isExpanded && (
                      <div className="px-3 pb-3 border-t border-surface-800">
                        <div className="mt-3 space-y-2">
                          <div className="text-xs text-surface-400 uppercase tracking-wide mb-2">
                            Model Breakdown
                          </div>
                          {phase.models.map((model, idx) => (
                            <div
                              key={idx}
                              className="flex items-center justify-between text-xs py-1.5 px-2 bg-surface-800 rounded"
                            >
                              <div className="flex items-center space-x-2">
                                <span className="text-surface-300 font-mono truncate max-w-[200px]" title={model.model}>
                                  {model.model}
                                </span>
                              </div>
                              <div className="flex items-center space-x-4">
                                <span className="text-surface-400">
                                  {model.completions} runs
                                </span>
                                <span className={getSuccessColor(model.success_rate)}>
                                  {model.success_rate}%
                                </span>
                                <span className="text-surface-400 font-mono">
                                  {formatCost(model.total_cost)}
                                </span>
                              </div>
                            </div>
                          ))}
                        </div>

                        {/* Insights for problematic phases */}
                        {phase.success_rate < 95 && (
                          <div className="mt-3 p-2 bg-yellow-900/20 border border-yellow-500/30 rounded text-xs">
                            <p className="text-yellow-400">
                              Low success rate — consider using a more capable base model
                            </p>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </>
      )}
    </div>
  )
}
