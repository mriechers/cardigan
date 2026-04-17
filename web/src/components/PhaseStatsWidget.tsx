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

interface EscalationReasonBreakdown {
  timeout: number
  api_error: number
  truncation: number
  other: number
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
  escalation_reasons?: EscalationReasonBreakdown
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
    by_phase: Record<string, { base_tier: number; escalated: number; rate: number }>
  }
}

interface PhaseCostEfficiency {
  phase: string
  base_tier: number
  base_tier_label: string
  total_runs: number
  runs_at_base: number
  runs_escalated: number
  avg_cost_at_base: number
  avg_cost_when_escalated: number
  escalation_waste: number
  total_cost: number
  recommendation: string | null
  suggested_tier: number | null
}

interface CostEfficiencyResponse {
  phases: PhaseCostEfficiency[]
  period_days: number
  total_escalation_waste: number
}

interface RoutingConfig {
  tier_labels: string[]
  tiers: string[]
  phase_base_tiers: Record<string, number>
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
  timestamp: { name: 'Timestamps', icon: '⏱️' },
}

/**
 * Widget displaying phase-level performance analytics.
 * Shows success rates, escalation patterns, and cost attribution by agent role.
 */
export default function PhaseStatsWidget({ className = '' }: PhaseStatsWidgetProps) {
  const [stats, setStats] = useState<PhaseStatsResponse | null>(null)
  const [costEfficiency, setCostEfficiency] = useState<CostEfficiencyResponse | null>(null)
  const [routingConfig, setRoutingConfig] = useState<RoutingConfig | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [days, setDays] = useState(30)
  const [expandedPhase, setExpandedPhase] = useState<string | null>(null)
  const [upgradingPhase, setUpgradingPhase] = useState<string | null>(null)

  const fetchStats = async () => {
    setLoading(true)
    try {
      const [statsRes, costRes, routingRes] = await Promise.all([
        fetch(`/api/langfuse/phase-stats?days=${days}`),
        fetch(`/api/langfuse/cost-efficiency?days=${days}`),
        fetch('/api/config/routing'),
      ])
      if (!statsRes.ok) {
        throw new Error(`HTTP ${statsRes.status}: ${statsRes.statusText}`)
      }
      const statsData = await statsRes.json()
      setStats(statsData)
      if (costRes.ok) {
        setCostEfficiency(await costRes.json())
      }
      if (routingRes.ok) {
        setRoutingConfig(await routingRes.json())
      }
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch stats')
      setStats(null)
    } finally {
      setLoading(false)
    }
  }

  const upgradeTier = async (phase: string, newTier: number) => {
    setUpgradingPhase(phase)
    try {
      const response = await fetch('/api/config/routing', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phase_base_tiers: { [phase]: newTier } }),
      })
      if (response.ok) {
        // Re-fetch stats to reflect the change
        await fetchStats()
      }
    } finally {
      setUpgradingPhase(null)
    }
  }

  useEffect(() => {
    fetchStats()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [days])

  // Get color based on success rate
  const getSuccessColor = (rate: number) => {
    if (rate >= 99) return 'text-green-400'
    if (rate >= 95) return 'text-yellow-400'
    if (rate >= 90) return 'text-orange-400'
    return 'text-red-400'
  }

  // Get bar color based on success rate
  const getBarColor = (rate: number) => {
    if (rate >= 99) return 'bg-green-500'
    if (rate >= 95) return 'bg-yellow-500'
    if (rate >= 90) return 'bg-orange-500'
    return 'bg-red-500'
  }

  // Get escalation indicator color
  const getEscalationColor = (rate: number) => {
    if (rate <= 5) return 'text-green-400'
    if (rate <= 20) return 'text-cyan-400'
    if (rate <= 50) return 'text-yellow-400'
    return 'text-orange-400'
  }

  // Format cost
  const formatCost = (cost: number) => {
    if (cost < 0.01) return `$${cost.toFixed(4)}`
    if (cost < 1) return `$${cost.toFixed(3)}`
    return `$${cost.toFixed(2)}`
  }

  // Get phase display info
  const getPhaseInfo = (phase: string) => {
    return PHASE_INFO[phase] || { name: phase, icon: '🤖' }
  }

  return (
    <div className={`bg-gray-800 rounded-lg border border-gray-700 p-4 ${className}`}>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="text-sm font-medium text-gray-400 uppercase tracking-wide">
            Agent Performance
          </h3>
          <p className="text-xs text-gray-500 mt-0.5">
            Success rates & escalation patterns
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
                        {/* Configured tier */}
                        {routingConfig && (
                          <div className="mt-3 flex items-center gap-2 text-xs">
                            <span className="text-gray-500">Configured:</span>
                            {(() => {
                              const configuredTier = routingConfig.phase_base_tiers?.[phase.phase] ?? 0
                              const configuredLabel = routingConfig.tier_labels?.[configuredTier] ?? `tier-${configuredTier}`
                              const TIER_COLORS: Record<number, string> = {
                                0: 'bg-green-500/20 text-green-400',
                                1: 'bg-cyan-500/20 text-cyan-400',
                                2: 'bg-purple-500/20 text-purple-400',
                                3: 'bg-blue-500/20 text-blue-400',
                              }
                              return (
                                <span className={`px-1.5 py-0.5 rounded text-[10px] ${TIER_COLORS[configuredTier] ?? 'bg-gray-500/20 text-gray-400'}`}>
                                  {configuredLabel}
                                </span>
                              )
                            })()}
                            {/* Flag if models used don't match configured tier's expected models */}
                            {(() => {
                              const configuredTier = routingConfig.phase_base_tiers?.[phase.phase] ?? 0
                              const actualTiers = phase.models.filter(m => m.tier !== null).map(m => m.tier!)
                              const hasHigherTier = actualTiers.some(t => t > configuredTier)
                              if (hasHigherTier && phase.escalation_rate > 0) {
                                return (
                                  <span className="text-yellow-500 text-[10px]">
                                    — {phase.escalation_rate}% ran above configured tier
                                  </span>
                                )
                              }
                              return null
                            })()}
                          </div>
                        )}

                        <div className="mt-3 space-y-2">
                          <div className="text-xs text-gray-500 uppercase tracking-wide mb-2">
                            Model Breakdown
                          </div>
                          {phase.models.map((model, idx) => (
                            <div
                              key={idx}
                              className="flex items-center justify-between text-xs py-1.5 px-2 bg-gray-800 rounded"
                            >
                              <div className="flex items-center space-x-2">
                                {model.tier_label && (
                                  <span className={`px-1.5 py-0.5 rounded text-[10px] ${
                                    model.tier === 2 ? 'bg-purple-500/20 text-purple-400'
                                    : model.tier === 3 ? 'bg-blue-500/20 text-blue-400'
                                    : model.tier === 0 ? 'bg-green-500/20 text-green-400'
                                    : 'bg-cyan-500/20 text-cyan-400'
                                  }`}>
                                    {model.tier_label}
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
                          ))}
                        </div>

                        {/* Escalation Reasons */}
                        {phase.escalation_rate > 0 && phase.escalation_reasons && (
                          <div className="mt-3">
                            <div className="text-xs text-gray-500 uppercase tracking-wide mb-2">
                              Escalation Reasons
                            </div>
                            <div className="flex flex-wrap gap-1.5">
                              {phase.escalation_reasons.timeout > 0 && (
                                <span className="px-2 py-0.5 rounded text-[11px] bg-orange-500/20 text-orange-400">
                                  timeout: {phase.escalation_reasons.timeout}
                                </span>
                              )}
                              {phase.escalation_reasons.api_error > 0 && (
                                <span className="px-2 py-0.5 rounded text-[11px] bg-red-500/20 text-red-400">
                                  api_error: {phase.escalation_reasons.api_error}
                                </span>
                              )}
                              {phase.escalation_reasons.truncation > 0 && (
                                <span className="px-2 py-0.5 rounded text-[11px] bg-yellow-500/20 text-yellow-400">
                                  truncation: {phase.escalation_reasons.truncation}
                                </span>
                              )}
                              {phase.escalation_reasons.other > 0 && (
                                <span className="px-2 py-0.5 rounded text-[11px] bg-gray-600/50 text-gray-400">
                                  other: {phase.escalation_reasons.other}
                                </span>
                              )}
                            </div>
                          </div>
                        )}

                        {/* Cost efficiency insights */}
                        {(() => {
                          const ce = costEfficiency?.phases.find(p => p.phase === phase.phase)
                          const hasIssue = phase.success_rate < 95 || (ce?.recommendation)
                          if (!hasIssue) return null
                          return (
                            <div className="mt-3 p-2 bg-yellow-900/20 border border-yellow-500/30 rounded text-xs space-y-1.5">
                              {phase.success_rate < 95 && (
                                <p className="text-yellow-400">
                                  Low success rate ({phase.success_rate}%) — consider a more capable base model
                                </p>
                              )}
                              {ce?.recommendation && (
                                <div>
                                  <p className="text-yellow-400">{ce.recommendation}</p>
                                  <div className="mt-1.5 flex items-center gap-3 text-gray-400">
                                    <span className="font-mono">
                                      Base avg: {formatCost(ce.avg_cost_at_base)}
                                    </span>
                                    <span className="font-mono">
                                      Escalated avg: {formatCost(ce.avg_cost_when_escalated)}
                                    </span>
                                    <span className="font-mono">
                                      Waste: {formatCost(ce.escalation_waste)}
                                    </span>
                                  </div>
                                  {ce.suggested_tier != null && (
                                    <button
                                      onClick={() => upgradeTier(phase.phase, ce.suggested_tier!)}
                                      disabled={upgradingPhase === phase.phase}
                                      className="mt-2 px-3 py-1 bg-yellow-600/30 hover:bg-yellow-600/50 text-yellow-300 rounded text-xs transition-colors disabled:opacity-50"
                                    >
                                      {upgradingPhase === phase.phase
                                        ? 'Upgrading...'
                                        : `Upgrade ${phase.phase} to ${routingConfig?.tier_labels?.[ce.suggested_tier!] ?? `tier ${ce.suggested_tier}`}`
                                      }
                                    </button>
                                  )}
                                </div>
                              )}
                            </div>
                          )
                        })()}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}

          {/* Footer */}
          <div className="mt-4 pt-3 border-t border-gray-700 flex justify-between items-center text-xs text-gray-600">
            <span>
              {stats.period_start ? new Date(stats.period_start).toLocaleDateString() : 'N/A'}
              {' - '}
              {stats.period_end ? new Date(stats.period_end).toLocaleDateString() : 'now'}
            </span>
            <span>
              Data from local session_stats
            </span>
          </div>
        </>
      )}
    </div>
  )
}
