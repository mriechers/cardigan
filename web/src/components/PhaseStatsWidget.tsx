import { useEffect, useState } from 'react'

interface PhaseModelStats {
  model: string
  tier: number | null
  tier_label: string | null
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
  escalation_rate: number
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
  escalation_summary: {
    by_phase: Record<string, { configured_tier: number; at_configured: number; escalated: number; rate: number }>
  }
  phase_base_tiers: Record<string, number>
  tier_labels: string[]
}

interface PhaseStatsWidgetProps {
  className?: string
}

// Phase display info
const PHASE_INFO: Record<string, { name: string; icon: string }> = {
  analyst: { name: 'Analyst', icon: '🔍' },
  formatter: { name: 'Formatter', icon: '📝' },
  seo: { name: 'SEO', icon: '🎯' },
  manager: { name: 'QA Manager', icon: '✅' },
  copy_editor: { name: 'Copy Editor', icon: '✏️' },
  chat: { name: 'Chat Editor', icon: '💬' },
  timestamp: { name: 'Timestamps', icon: '⏱️' },
}

// Tier color styling
const TIER_STYLES = [
  { badge: 'bg-green-900/30 text-green-400 border-green-500/30', text: 'text-green-400' },
  { badge: 'bg-cyan-900/30 text-cyan-400 border-cyan-500/30', text: 'text-cyan-400' },
  { badge: 'bg-purple-900/30 text-purple-400 border-purple-500/30', text: 'text-purple-400' },
]

/**
 * Unified agent performance widget.
 * Shows each agent's configured tier, success rate, escalation rate, and model breakdown.
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

  const getEscalationColor = (rate: number) => {
    if (rate <= 5) return 'text-green-400'
    if (rate <= 20) return 'text-cyan-400'
    if (rate <= 50) return 'text-yellow-400'
    return 'text-orange-400'
  }

  const formatCost = (cost: number) => {
    if (cost < 0.01) return `$${cost.toFixed(4)}`
    if (cost < 1) return `$${cost.toFixed(3)}`
    return `$${cost.toFixed(2)}`
  }

  const getPhaseInfo = (phase: string) => {
    return PHASE_INFO[phase] || { name: phase, icon: '🤖' }
  }

  const getTierBadge = (tierIdx: number) => {
    const style = TIER_STYLES[tierIdx] || TIER_STYLES[0]
    const label = stats?.tier_labels?.[tierIdx] || `Tier ${tierIdx}`
    return { style, label }
  }

  const getModelTierStyle = (tier: number | null) => {
    if (tier === null || tier === undefined) return TIER_STYLES[0]
    return TIER_STYLES[tier] || TIER_STYLES[0]
  }

  return (
    <div className={`bg-gray-800 rounded-lg border border-gray-700 p-4 ${className}`}>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="text-sm font-medium text-gray-400 uppercase tracking-wide">
            Agent Performance
          </h3>
          <p className="text-xs text-gray-500 mt-0.5">
            Model usage & escalation rates relative to configured tier
          </p>
        </div>
        <div className="flex items-center space-x-2">
          <select
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className="bg-gray-700 text-gray-300 text-xs rounded px-2 py-1 border border-gray-600 focus:outline-none focus:ring-1 focus:ring-blue-500"
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
            className="text-xs text-gray-400 hover:text-white disabled:opacity-50"
            aria-label="Refresh stats"
          >
            {loading ? '...' : 'Refresh'}
          </button>
        </div>
      </div>

      {/* Loading state */}
      {loading && !stats && (
        <div className="animate-pulse space-y-3">
          <div className="h-12 bg-gray-700 rounded"></div>
          <div className="h-12 bg-gray-700 rounded"></div>
          <div className="h-12 bg-gray-700 rounded"></div>
        </div>
      )}

      {/* Error state */}
      {error && !loading && (
        <div className="text-center py-4">
          <p className="text-red-400 text-sm">{error}</p>
          <button
            onClick={fetchStats}
            className="mt-2 text-xs text-blue-400 hover:text-blue-300"
          >
            Try again
          </button>
        </div>
      )}

      {/* Stats display */}
      {stats && !error && (
        <>
          {/* Summary bar */}
          <div className="flex justify-between text-sm mb-4 pb-3 border-b border-gray-700">
            <div>
              <span className="text-gray-400">Total:</span>{' '}
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
              <span className="text-gray-400">Cost:</span>{' '}
              <span className="text-white font-medium">{formatCost(stats.total_cost)}</span>
            </div>
          </div>

          {/* Phase list */}
          {stats.phases.length === 0 ? (
            <p className="text-gray-500 text-sm text-center py-4">
              No phase data for this period
            </p>
          ) : (
            <div className="space-y-2">
              {stats.phases.map((phase) => {
                const info = getPhaseInfo(phase.phase)
                const isExpanded = expandedPhase === phase.phase
                const configuredTier = stats.phase_base_tiers?.[phase.phase] ?? 0
                const tierBadge = getTierBadge(configuredTier)

                return (
                  <div key={phase.phase} className="bg-gray-900 rounded-lg overflow-hidden">
                    {/* Main row - clickable */}
                    <button
                      onClick={() => setExpandedPhase(isExpanded ? null : phase.phase)}
                      className="w-full p-3 flex items-center justify-between hover:bg-gray-800/50 transition-colors"
                    >
                      <div className="flex items-center space-x-3">
                        <span className="text-lg">{info.icon}</span>
                        <div className="text-left">
                          <div className="flex items-center space-x-2">
                            <span className="text-white font-medium">{info.name}</span>
                            <span className={`text-[10px] px-1.5 py-0.5 rounded border ${tierBadge.style.badge}`}>
                              {tierBadge.label}
                            </span>
                            <span className="text-xs text-gray-500">
                              {phase.total_completions} runs
                            </span>
                          </div>
                          {/* Progress bar */}
                          <div className="flex items-center space-x-2 mt-1">
                            <div className="w-32 h-1.5 bg-gray-700 rounded-full overflow-hidden">
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
                        {/* Escalation rate */}
                        <div>
                          <div className="text-xs text-gray-500">Escalated</div>
                          <div className={`text-sm font-mono ${getEscalationColor(phase.escalation_rate)}`}>
                            {phase.escalation_rate}%
                          </div>
                        </div>
                        {/* Cost */}
                        <div>
                          <div className="text-xs text-gray-500">Cost</div>
                          <div className="text-sm font-mono text-white">
                            {formatCost(phase.total_cost)}
                          </div>
                        </div>
                        {/* Expand indicator */}
                        <span className={`text-gray-500 transition-transform ${isExpanded ? 'rotate-180' : ''}`}>
                          ▼
                        </span>
                      </div>
                    </button>

                    {/* Expanded details */}
                    {isExpanded && (
                      <div className="px-3 pb-3 border-t border-gray-800">
                        <div className="mt-3 space-y-2">
                          <div className="text-xs text-gray-500 uppercase tracking-wide mb-2">
                            Model Breakdown
                          </div>
                          {phase.models.map((model, idx) => {
                            const modelTierStyle = getModelTierStyle(model.tier)
                            const isEscalated = model.tier !== null && model.tier > configuredTier
                            return (
                              <div
                                key={idx}
                                className="flex items-center justify-between text-xs py-1.5 px-2 bg-gray-800 rounded"
                              >
                                <div className="flex items-center space-x-2">
                                  {model.tier !== null && (
                                    <span className={`px-1.5 py-0.5 rounded text-[10px] ${modelTierStyle.badge}`}>
                                      {stats.tier_labels?.[model.tier] || `T${model.tier}`}
                                      {isEscalated && ' ↑'}
                                    </span>
                                  )}
                                  <span className="text-gray-300 font-mono truncate max-w-[200px]" title={model.model}>
                                    {model.model}
                                  </span>
                                </div>
                                <div className="flex items-center space-x-4">
                                  <span className="text-gray-500">
                                    {model.completions} runs
                                  </span>
                                  <span className={getSuccessColor(model.success_rate)}>
                                    {model.success_rate}%
                                  </span>
                                  <span className="text-gray-400 font-mono">
                                    {formatCost(model.total_cost)}
                                  </span>
                                </div>
                              </div>
                            )
                          })}
                        </div>

                        {/* Insights for problematic phases */}
                        {(phase.success_rate < 95 || phase.escalation_rate > 30) && (
                          <div className="mt-3 p-2 bg-yellow-900/20 border border-yellow-500/30 rounded text-xs">
                            {phase.success_rate < 95 && (
                              <p className="text-yellow-400">
                                Low success rate — consider using a more capable base model
                              </p>
                            )}
                            {phase.escalation_rate > 30 && phase.escalation_rate < 100 && (
                              <p className="text-yellow-400">
                                High escalation rate — base tier may be underpowered for this task
                              </p>
                            )}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}

          {/* Tier legend */}
          {stats.tier_labels && stats.tier_labels.length > 0 && (
            <div className="mt-4 pt-3 border-t border-gray-700 flex items-center justify-between">
              <div className="flex items-center space-x-4 text-xs text-gray-500">
                {stats.tier_labels.map((label, idx) => {
                  const style = TIER_STYLES[idx] || TIER_STYLES[0]
                  return (
                    <span key={idx} className={style.text}>{label}</span>
                  )
                })}
              </div>
              <span className="text-xs text-gray-600">
                Escalation = ran above configured tier
              </span>
            </div>
          )}
        </>
      )}
    </div>
  )
}
