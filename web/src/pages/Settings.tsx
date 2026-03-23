import { useEffect, useState, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { usePreferences, TextSize } from '../context/PreferencesContext'
import { AGENT_INFO } from '../constants/agents'

interface DurationThreshold {
  max_minutes: number | null
  tier: number
}

interface EscalationConfig {
  enabled: boolean
  on_failure: boolean
  on_timeout: boolean
  timeout_seconds: number
  max_retries_per_tier: number
}

interface RoutingConfig {
  tiers: string[]
  tier_labels: string[]
  duration_thresholds: DurationThreshold[]
  phase_base_tiers: Record<string, number>
  escalation: EscalationConfig
}

interface WorkerConfig {
  max_concurrent_jobs: number
  poll_interval_seconds: number
  heartbeat_interval_seconds: number
}

interface IngestConfig {
  enabled: boolean
  scan_interval_hours: number
  scan_time: string  // "HH:MM" format
  last_scan_at: string | null
  last_scan_success: boolean | null
  server_url: string
  directories: string[]
  ignore_directories: string[]
  next_scan_at: string | null
}

interface IngestConfigUpdate {
  enabled?: boolean
  scan_interval_hours?: number  // 1-168
  scan_time?: string  // "HH:MM" format
}

const TIER_COLORS = ['green', 'cyan', 'purple'] as const
const TIER_STYLES: Record<string, { bg: string; border: string; text: string }> = {
  green: { bg: 'bg-green-900/20', border: 'border-green-500/30', text: 'text-green-400' },
  cyan: { bg: 'bg-cyan-900/20', border: 'border-cyan-500/30', text: 'text-cyan-400' },
  purple: { bg: 'bg-purple-900/20', border: 'border-purple-500/30', text: 'text-purple-400' },
}

type TabId = 'agents' | 'routing' | 'worker' | 'ingest' | 'system' | 'accessibility'

const TABS: { id: TabId; label: string; icon: string }[] = [
  { id: 'agents', label: 'Agents', icon: '🤖' },
  { id: 'routing', label: 'Routing', icon: '🔀' },
  { id: 'worker', label: 'Worker', icon: '⚙️' },
  { id: 'ingest', label: 'Ingest', icon: '📥' },
  { id: 'system', label: 'System', icon: '🖥️' },
  { id: 'accessibility', label: 'Accessibility', icon: '♿' },
]

interface ComponentStatus {
  name: string
  running: boolean
  pid: number | null
}

interface SystemStatus {
  api: ComponentStatus
  worker: ComponentStatus
  watcher: ComponentStatus
}

export default function Settings() {
  const { preferences, updatePreferences } = usePreferences()
  const [routing, setRouting] = useState<RoutingConfig | null>(null)
  const [worker, setWorker] = useState<WorkerConfig | null>(null)
  const [ingestConfig, setIngestConfig] = useState<IngestConfig | null>(null)
  const [systemStatus, setSystemStatus] = useState<SystemStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<TabId>('agents')

  // Track unsaved changes
  const [pendingRouting, setPendingRouting] = useState<Partial<RoutingConfig> | null>(null)
  const [pendingWorker, setPendingWorker] = useState<Partial<WorkerConfig> | null>(null)
  const [pendingIngest, setPendingIngest] = useState<Partial<IngestConfigUpdate> | null>(null)

  const fetchConfig = useCallback(async () => {
    try {
      setLoading(true)
      const [routingRes, workerRes] = await Promise.all([
        fetch('/api/config/routing'),
        fetch('/api/config/worker')
      ])

      if (!routingRes.ok) {
        throw new Error('Failed to fetch routing configuration')
      }
      if (!workerRes.ok) {
        throw new Error('Failed to fetch worker configuration')
      }

      const routingData = await routingRes.json()
      const workerData = await workerRes.json()
      setRouting(routingData)
      setWorker(workerData)
      setPendingRouting(null)
      setPendingWorker(null)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }, [])

  const fetchIngestConfig = useCallback(async () => {
    try {
      const response = await fetch('/api/ingest/config')
      if (response.ok) {
        setIngestConfig(await response.json())
      }
    } catch (err) {
      console.error('Failed to fetch ingest config:', err)
    }
  }, [])

  const fetchSystemStatus = useCallback(async () => {
    try {
      const res = await fetch('/api/system/status')
      if (res.ok) {
        const data = await res.json()
        setSystemStatus(data)
      }
    } catch (err) {
      console.error('Failed to fetch system status:', err)
    }
  }, [])


  useEffect(() => {
    fetchConfig()
    fetchIngestConfig()
    fetchSystemStatus()
  }, [fetchConfig, fetchIngestConfig, fetchSystemStatus])

  // Poll system status when on System tab
  useEffect(() => {
    if (activeTab !== 'system') return

    const interval = setInterval(fetchSystemStatus, 5000)
    return () => clearInterval(interval)
  }, [activeTab, fetchSystemStatus])

  const handlePhaseBaseTierChange = (phase: string, tier: number) => {
    const current = pendingRouting?.phase_base_tiers || routing?.phase_base_tiers || {}
    setPendingRouting({
      ...pendingRouting,
      phase_base_tiers: { ...current, [phase]: tier }
    })
  }

  const handleThresholdChange = (index: number, value: number | null) => {
    const current = pendingRouting?.duration_thresholds || routing?.duration_thresholds || []
    const updated = [...current]
    updated[index] = { ...updated[index], max_minutes: value }
    setPendingRouting({ ...pendingRouting, duration_thresholds: updated })
  }

  const handleEscalationChange = (key: keyof EscalationConfig, value: boolean | number) => {
    const current = pendingRouting?.escalation || routing?.escalation || {
      enabled: true, on_failure: true, on_timeout: true, timeout_seconds: 120, max_retries_per_tier: 1
    }
    setPendingRouting({
      ...pendingRouting,
      escalation: { ...current, [key]: value }
    })
  }

  const handleWorkerChange = (key: keyof WorkerConfig, value: number) => {
    setPendingWorker({
      ...pendingWorker,
      [key]: value
    })
  }

  const handleIngestChange = (key: keyof IngestConfigUpdate, value: boolean | number | string) => {
    setPendingIngest({
      ...pendingIngest,
      [key]: value
    })
  }

  const handleSave = async () => {
    setSaving(true)
    setError(null)
    setSuccess(null)

    try {
      // Save routing config if changed
      if (pendingRouting) {
        const res = await fetch('/api/config/routing', {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(pendingRouting)
        })
        if (!res.ok) {
          let detail = 'Failed to save routing config'
          try {
            const data = await res.json()
            detail = data.detail || detail
          } catch {
            // Response wasn't JSON
          }
          throw new Error(detail)
        }
      }

      // Save worker config if changed
      if (pendingWorker) {
        const res = await fetch('/api/config/worker', {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(pendingWorker)
        })
        if (!res.ok) {
          let detail = 'Failed to save worker config'
          try {
            const data = await res.json()
            detail = data.detail || detail
          } catch {
            // Response wasn't JSON
          }
          throw new Error(detail)
        }
      }

      // Save ingest config if changed
      if (pendingIngest) {
        const res = await fetch('/api/ingest/config', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(pendingIngest)
        })
        if (!res.ok) throw new Error('Failed to save ingest config')
      }

      setSuccess('Settings saved successfully. Restart workers to apply changes.')
      await fetchConfig()
      await fetchIngestConfig()
      setPendingIngest(null)
      setTimeout(() => setSuccess(null), 5000)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setSaving(false)
    }
  }

  const handleReset = () => {
    setPendingRouting(null)
    setPendingWorker(null)
    setPendingIngest(null)
  }

  const hasChanges = pendingRouting !== null || pendingWorker !== null || pendingIngest !== null

  const getCurrentWorker = (): WorkerConfig => {
    return {
      max_concurrent_jobs: pendingWorker?.max_concurrent_jobs ?? worker?.max_concurrent_jobs ?? 3,
      poll_interval_seconds: pendingWorker?.poll_interval_seconds ?? worker?.poll_interval_seconds ?? 5,
      heartbeat_interval_seconds: pendingWorker?.heartbeat_interval_seconds ?? worker?.heartbeat_interval_seconds ?? 60
    }
  }

  const getCurrentIngest = (): IngestConfig => {
    return {
      enabled: pendingIngest?.enabled ?? ingestConfig?.enabled ?? false,
      scan_interval_hours: pendingIngest?.scan_interval_hours ?? ingestConfig?.scan_interval_hours ?? 24,
      scan_time: pendingIngest?.scan_time ?? ingestConfig?.scan_time ?? '02:00',
      last_scan_at: ingestConfig?.last_scan_at ?? null,
      last_scan_success: ingestConfig?.last_scan_success ?? null,
      server_url: ingestConfig?.server_url ?? '',
      directories: ingestConfig?.directories ?? [],
      ignore_directories: ingestConfig?.ignore_directories ?? [],
      next_scan_at: ingestConfig?.next_scan_at ?? null
    }
  }

  const getCurrentPhaseBaseTier = (phase: string): number => {
    return pendingRouting?.phase_base_tiers?.[phase] ?? routing?.phase_base_tiers?.[phase] ?? 0
  }

  const getCurrentThresholds = (): DurationThreshold[] => {
    return pendingRouting?.duration_thresholds || routing?.duration_thresholds || []
  }

  const getCurrentEscalation = (): EscalationConfig => {
    return pendingRouting?.escalation || routing?.escalation || {
      enabled: true, on_failure: true, on_timeout: true, timeout_seconds: 120, max_retries_per_tier: 1
    }
  }

  const getTierLabel = (tier: number): string => {
    return routing?.tier_labels?.[tier] || `tier-${tier}`
  }

  const getTierColor = (tier: number): string => {
    return TIER_COLORS[tier] || 'cyan'
  }

  const formatDateTime = (isoString: string | null): string => {
    if (!isoString) return 'Never'
    const date = new Date(isoString)
    return date.toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      hour12: true
    })
  }

  if (loading) {
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-bold text-white">Settings</h1>
        <div className="bg-gray-800 rounded-lg border border-gray-700 p-6">
          <p className="text-gray-400 animate-pulse">Loading configuration...</p>
        </div>
      </div>
    )
  }

  // Toggle component for cleaner code
  const Toggle = ({ checked, onChange, label }: { checked: boolean; onChange: () => void; label: string }) => (
    <button
      onClick={onChange}
      role="switch"
      aria-checked={checked}
      aria-label={label}
      className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
        checked ? 'bg-blue-600' : 'bg-gray-600'
      }`}
    >
      <span
        aria-hidden="true"
        className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
          checked ? 'translate-x-6' : 'translate-x-1'
        }`}
      />
    </button>
  )

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-white">Settings</h1>
        {hasChanges && (
          <div className="flex items-center space-x-3">
            <button
              onClick={handleReset}
              className="px-4 py-2 bg-gray-700 hover:bg-gray-600 text-white rounded-md text-sm transition-colors"
            >
              Reset
            </button>
            <button
              onClick={handleSave}
              disabled={saving}
              className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white rounded-md text-sm transition-colors"
            >
              {saving ? 'Saving...' : 'Save Changes'}
            </button>
          </div>
        )}
      </div>

      {/* Status Messages */}
      {error && (
        <div role="alert" aria-live="assertive" className="bg-red-900/20 border border-red-500/30 rounded-lg p-4">
          <p className="text-red-400">{error}</p>
        </div>
      )}
      {success && (
        <div role="status" aria-live="polite" className="bg-green-900/20 border border-green-500/30 rounded-lg p-4">
          <p className="text-green-400">{success}</p>
        </div>
      )}

      {/* Tab Navigation */}
      <div className="border-b border-gray-700">
        <nav className="flex space-x-1" role="tablist" aria-label="Settings sections">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              role="tab"
              aria-selected={activeTab === tab.id}
              aria-controls={`panel-${tab.id}`}
              onClick={() => setActiveTab(tab.id)}
              className={`px-4 py-3 text-sm font-medium rounded-t-lg transition-colors ${
                activeTab === tab.id
                  ? 'bg-gray-800 text-white border-b-2 border-blue-500'
                  : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800/50'
              }`}
            >
              <span className="mr-2">{tab.icon}</span>
              {tab.label}
            </button>
          ))}
        </nav>
      </div>

      {/* Tab Panels */}
      <div role="tabpanel" id={`panel-${activeTab}`} aria-labelledby={activeTab}>
        {/* AGENTS TAB */}
        {activeTab === 'agents' && (
          <div className="space-y-6">
            {/* Agent Base Tier Assignment */}
            <div className="bg-gray-800 rounded-lg border border-gray-700 p-6">
              <h2 className="text-lg font-semibold text-white mb-4">Agent Base Tiers</h2>
              <p className="text-sm text-gray-400 mb-6">
                Set the starting tier for each agent. Short transcripts will use this tier.
                Longer transcripts may automatically escalate based on duration thresholds.
              </p>

              <div className="space-y-4">
                {AGENT_INFO.map((agent) => {
                  const currentTier = getCurrentPhaseBaseTier(agent.id)
                  const color = getTierColor(currentTier)
                  const styles = TIER_STYLES[color]

                  return (
                    <div key={agent.id} className="flex items-center justify-between p-4 bg-gray-900 rounded-lg">
                      <div className="flex items-center space-x-4">
                        <div className="w-10 h-10 rounded-full bg-gray-800 flex items-center justify-center text-lg">
                          {agent.icon}
                        </div>
                        <div>
                          <div className="font-medium text-white">{agent.name}</div>
                          <div className="text-sm text-gray-400">{agent.description}</div>
                        </div>
                      </div>

                      <label htmlFor={`tier-${agent.id}`} className="sr-only">
                        Base tier for {agent.name}
                      </label>
                      <select
                        id={`tier-${agent.id}`}
                        value={currentTier}
                        onChange={(e) => handlePhaseBaseTierChange(agent.id, parseInt(e.target.value))}
                        className={`px-3 py-2 rounded-md border text-sm font-medium ${styles.bg} ${styles.border} ${styles.text}`}
                        aria-label={`Select base tier for ${agent.name} agent`}
                      >
                        {routing?.tier_labels?.map((label, idx) => (
                          <option key={idx} value={idx} className="bg-gray-800 text-white">
                            {label}
                          </option>
                        ))}
                      </select>
                    </div>
                  )
                })}
              </div>

              <div className="mt-4 flex items-center space-x-6 text-xs text-gray-400">
                {routing?.tier_labels?.map((label, idx) => {
                  const color = getTierColor(idx)
                  return (
                    <div key={idx} className="flex items-center space-x-2">
                      <span className="w-2 h-2 rounded-full" style={{backgroundColor: color === 'green' ? '#22c55e' : color === 'cyan' ? '#06b6d4' : '#a855f7'}} />
                      <span>{label}</span>
                    </div>
                  )
                })}
              </div>
            </div>
          </div>
        )}

        {/* ROUTING TAB */}
        {activeTab === 'routing' && (
          <div className="space-y-6">
            {/* Duration-Based Tier Escalation */}
            <div className="bg-gray-800 rounded-lg border border-gray-700 p-6">
              <h2 className="text-lg font-semibold text-white mb-4">Duration-Based Tier Selection</h2>
              <p className="text-sm text-gray-400 mb-6">
                Set duration thresholds for automatic tier escalation. Longer transcripts require
                more capable models.
              </p>

              <div className="space-y-4">
                {getCurrentThresholds().map((threshold, idx) => {
                  const label = getTierLabel(threshold.tier)
                  const color = getTierColor(threshold.tier)
                  const styles = TIER_STYLES[color]

                  return (
                    <div key={idx} className={`p-4 rounded-lg border ${styles.bg} ${styles.border}`}>
                      <div className="flex items-center justify-between mb-2">
                        <span className={`font-medium ${styles.text}`}>
                          Tier {threshold.tier}: {label}
                        </span>
                        <span className="text-gray-400 text-sm">
                          {threshold.max_minutes === null
                            ? 'Unlimited duration'
                            : `Up to ${threshold.max_minutes} minutes`}
                        </span>
                      </div>
                      {threshold.max_minutes !== null && (
                        <>
                          <label htmlFor={`threshold-${idx}`} className="sr-only">
                            Maximum duration in minutes for {label}
                          </label>
                          <input
                            id={`threshold-${idx}`}
                            type="range"
                            min="5"
                            max="60"
                            step="5"
                            value={threshold.max_minutes}
                            onChange={(e) => handleThresholdChange(idx, parseInt(e.target.value))}
                            className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
                            aria-valuemin={5}
                            aria-valuemax={60}
                            aria-valuenow={threshold.max_minutes}
                          />
                        </>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>

            {/* Failure-Based Escalation */}
            <div className="bg-gray-800 rounded-lg border border-gray-700 p-6">
              <h2 className="text-lg font-semibold text-white mb-4">Failure-Based Escalation</h2>
              <p className="text-sm text-gray-400 mb-6">
                When a model fails or times out, automatically retry with the next tier up.
              </p>

              <div className="space-y-4">
                <div className="flex items-center justify-between p-4 bg-gray-900 rounded-lg">
                  <div>
                    <div className="font-medium text-white">Enable Auto-Escalation</div>
                    <div className="text-sm text-gray-400">Automatically retry with higher tiers on failure</div>
                  </div>
                  <Toggle
                    checked={getCurrentEscalation().enabled}
                    onChange={() => handleEscalationChange('enabled', !getCurrentEscalation().enabled)}
                    label="Enable auto-escalation"
                  />
                </div>

                {getCurrentEscalation().enabled && (
                  <>
                    <div className="flex items-center justify-between p-4 bg-gray-900 rounded-lg">
                      <div>
                        <div className="font-medium text-white">Escalate on Failure</div>
                        <div className="text-sm text-gray-400">Retry with next tier when LLM returns an error</div>
                      </div>
                      <Toggle
                        checked={getCurrentEscalation().on_failure}
                        onChange={() => handleEscalationChange('on_failure', !getCurrentEscalation().on_failure)}
                        label="Escalate on failure"
                      />
                    </div>

                    <div className="flex items-center justify-between p-4 bg-gray-900 rounded-lg">
                      <div>
                        <div className="font-medium text-white">Escalate on Timeout</div>
                        <div className="text-sm text-gray-400">Retry with next tier when request times out</div>
                      </div>
                      <Toggle
                        checked={getCurrentEscalation().on_timeout}
                        onChange={() => handleEscalationChange('on_timeout', !getCurrentEscalation().on_timeout)}
                        label="Escalate on timeout"
                      />
                    </div>

                    <div className="p-4 bg-gray-900 rounded-lg">
                      <div className="flex items-center justify-between mb-2">
                        <label htmlFor="timeout-duration" className="font-medium text-white">Timeout Duration</label>
                        <span className="text-gray-400">{getCurrentEscalation().timeout_seconds} seconds</span>
                      </div>
                      <input
                        id="timeout-duration"
                        type="range"
                        min="30"
                        max="300"
                        step="30"
                        value={getCurrentEscalation().timeout_seconds}
                        onChange={(e) => handleEscalationChange('timeout_seconds', parseInt(e.target.value))}
                        className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
                        aria-valuemin={30}
                        aria-valuemax={300}
                        aria-valuenow={getCurrentEscalation().timeout_seconds}
                      />
                      <div className="flex justify-between text-xs text-gray-400 mt-1">
                        <span>30s</span>
                        <span>2 min</span>
                        <span>5 min</span>
                      </div>
                    </div>
                  </>
                )}
              </div>
            </div>

            {/* OpenRouter Preset Note */}
            <div className="bg-gray-800 rounded-lg border border-gray-700 p-4">
              <div className="flex items-start space-x-3">
                <span className="text-yellow-400 text-xl">💡</span>
                <div>
                  <h3 className="text-sm font-medium text-white">Managing OpenRouter Presets</h3>
                  <p className="text-xs text-gray-400 mt-1">
                    These presets are configured in your OpenRouter account. To modify the models in each
                    preset tier, visit{' '}
                    <a
                      href="https://openrouter.ai/settings/presets"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-blue-400 hover:text-blue-300"
                    >
                      openrouter.ai/settings/presets
                    </a>
                  </p>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* WORKER TAB */}
        {activeTab === 'worker' && (
          <div className="space-y-6">
            <div className="bg-gray-800 rounded-lg border border-gray-700 p-6">
              <h2 className="text-lg font-semibold text-white mb-4">Worker Settings</h2>
              <p className="text-sm text-gray-400 mb-6">
                Configure job processing concurrency. Changes require worker restart.
              </p>

              <div className="space-y-4">
                {/* Concurrent Jobs */}
                <div className="p-4 bg-gray-900 rounded-lg">
                  <div className="flex items-center justify-between mb-2">
                    <div>
                      <label htmlFor="concurrent-jobs" className="font-medium text-white">Concurrent Jobs</label>
                      <div className="text-sm text-gray-400">Process multiple jobs simultaneously</div>
                    </div>
                    <span className="text-2xl font-bold text-cyan-400">{getCurrentWorker().max_concurrent_jobs}</span>
                  </div>
                  <input
                    id="concurrent-jobs"
                    type="range"
                    min="1"
                    max="5"
                    step="1"
                    value={getCurrentWorker().max_concurrent_jobs}
                    onChange={(e) => handleWorkerChange('max_concurrent_jobs', parseInt(e.target.value))}
                    className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
                    aria-valuemin={1}
                    aria-valuemax={5}
                    aria-valuenow={getCurrentWorker().max_concurrent_jobs}
                  />
                  <div className="flex justify-between text-xs text-gray-400 mt-1">
                    <span>1 (safe)</span>
                    <span>3 (default)</span>
                    <span>5 (max)</span>
                  </div>
                </div>

                {/* Poll Interval */}
                <div className="p-4 bg-gray-900 rounded-lg">
                  <div className="flex items-center justify-between mb-2">
                    <div>
                      <label htmlFor="poll-interval" className="font-medium text-white">Poll Interval</label>
                      <div className="text-sm text-gray-400">Seconds between queue checks</div>
                    </div>
                    <span className="text-gray-300">{getCurrentWorker().poll_interval_seconds}s</span>
                  </div>
                  <input
                    id="poll-interval"
                    type="range"
                    min="1"
                    max="30"
                    step="1"
                    value={getCurrentWorker().poll_interval_seconds}
                    onChange={(e) => handleWorkerChange('poll_interval_seconds', parseInt(e.target.value))}
                    className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
                    aria-valuemin={1}
                    aria-valuemax={30}
                    aria-valuenow={getCurrentWorker().poll_interval_seconds}
                  />
                  <div className="flex justify-between text-xs text-gray-400 mt-1">
                    <span>1s</span>
                    <span>15s</span>
                    <span>30s</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* INGEST TAB */}
        {activeTab === 'ingest' && (
          <div className="space-y-6">
            <div className="bg-gray-800 rounded-lg border border-gray-700 p-6">
              <h2 className="text-lg font-semibold text-white mb-4">Ingest Scanner</h2>
              <p className="text-sm text-gray-400 mb-6">
                Automatically scan network locations for new transcript files and add them to the processing queue.
              </p>

              <div className="space-y-4">
                {/* Enable Toggle */}
                <div className="flex items-center justify-between p-4 bg-gray-900 rounded-lg">
                  <div>
                    <div className="font-medium text-white">Enable Scanner</div>
                    <div className="text-sm text-gray-400">Automatically discover and ingest new transcripts</div>
                  </div>
                  <Toggle
                    checked={getCurrentIngest().enabled}
                    onChange={() => handleIngestChange('enabled', !getCurrentIngest().enabled)}
                    label="Enable ingest scanner"
                  />
                </div>

                {/* Scan Interval */}
                <div className="p-4 bg-gray-900 rounded-lg">
                  <div className="flex items-center justify-between mb-2">
                    <div>
                      <label htmlFor="scan-interval" className="font-medium text-white">Scan Interval</label>
                      <div className="text-sm text-gray-400">Hours between automatic scans</div>
                    </div>
                    <span className="text-gray-300">{getCurrentIngest().scan_interval_hours}h</span>
                  </div>
                  <input
                    id="scan-interval"
                    type="range"
                    min="1"
                    max="168"
                    step="1"
                    value={getCurrentIngest().scan_interval_hours}
                    onChange={(e) => handleIngestChange('scan_interval_hours', parseInt(e.target.value))}
                    className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
                    aria-valuemin={1}
                    aria-valuemax={168}
                    aria-valuenow={getCurrentIngest().scan_interval_hours}
                  />
                  <div className="flex justify-between text-xs text-gray-400 mt-1">
                    <span>1h</span>
                    <span>24h (daily)</span>
                    <span>168h (weekly)</span>
                  </div>
                </div>

                {/* Scan Time */}
                <div className="p-4 bg-gray-900 rounded-lg">
                  <div className="mb-2">
                    <label htmlFor="scan-time" className="font-medium text-white block">Preferred Scan Time</label>
                    <div className="text-sm text-gray-400">Daily time to run scheduled scans (24-hour format)</div>
                  </div>
                  <input
                    id="scan-time"
                    type="time"
                    value={getCurrentIngest().scan_time}
                    onChange={(e) => handleIngestChange('scan_time', e.target.value)}
                    className="px-3 py-2 bg-gray-800 border border-gray-600 rounded-md text-white focus:border-blue-500 focus:outline-none"
                  />
                </div>

                {/* Server URL (read-only) */}
                <div className="p-4 bg-gray-900 rounded-lg">
                  <div className="mb-2">
                    <div className="font-medium text-white">Server URL</div>
                    <div className="text-sm text-gray-400">Remote file server location</div>
                  </div>
                  <code className="block p-2 bg-gray-800 rounded text-cyan-400 text-sm">
                    {getCurrentIngest().server_url || 'Not configured'}
                  </code>
                </div>

                {/* Last Scan Info */}
                {getCurrentIngest().last_scan_at && (
                  <div className="p-4 bg-gray-900 rounded-lg">
                    <div className="flex items-center justify-between mb-2">
                      <div className="font-medium text-white">Last Scan</div>
                      <span className={`px-2 py-1 rounded text-xs font-medium ${
                        getCurrentIngest().last_scan_success
                          ? 'bg-green-900/20 text-green-400'
                          : 'bg-red-900/20 text-red-400'
                      }`}>
                        {getCurrentIngest().last_scan_success ? 'Success' : 'Failed'}
                      </span>
                    </div>
                    <div className="text-sm text-gray-400">
                      {formatDateTime(getCurrentIngest().last_scan_at)}
                    </div>
                  </div>
                )}

                {/* Next Scheduled Scan */}
                {getCurrentIngest().next_scan_at && (
                  <div className="p-4 bg-gray-900 rounded-lg">
                    <div className="font-medium text-white mb-2">Next Scheduled Scan</div>
                    <div className="text-sm text-gray-400">
                      {formatDateTime(getCurrentIngest().next_scan_at)}
                    </div>
                  </div>
                )}
              </div>
            </div>

            {/* Link to Ready for Work page */}
            <div className="bg-gray-800 rounded-lg border border-gray-700 p-6">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="text-lg font-medium text-white">Ready for Work</h3>
                  <p className="text-sm text-gray-400 mt-1">
                    View and queue transcripts from the ingest server with search and filtering.
                  </p>
                </div>
                <Link
                  to="/ready"
                  className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg transition-colors font-medium"
                >
                  Open Ready for Work
                </Link>
              </div>
            </div>

            {/* Screengrab Info */}
            <div className="bg-gray-800 rounded-lg border border-gray-700 p-6">
              <div className="flex items-start space-x-3">
                <div className="flex-shrink-0 w-10 h-10 bg-purple-500/20 rounded-full flex items-center justify-center">
                  <svg className="w-5 h-5 text-purple-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
                  </svg>
                </div>
                <div>
                  <h3 className="text-lg font-medium text-white">Screengrab Attachments</h3>
                  <p className="text-sm text-gray-400 mt-1">
                    Screengrabs are now attached contextually from the job detail page. When a completed job has matching screengrabs available, you'll see an "Attach Screengrabs" button in the job header.
                  </p>
                </div>
              </div>
            </div>

            {/* Configuration Note */}
            <div className="bg-gray-800 rounded-lg border border-gray-700 p-4">
              <div className="flex items-start space-x-3">
                <span className="text-yellow-400 text-xl">💡</span>
                <div>
                  <h3 className="text-sm font-medium text-white">Server Configuration</h3>
                  <p className="text-xs text-gray-400 mt-1">
                    Network paths and credentials are managed in server environment variables.
                    Contact your system administrator to modify the server URL or monitored directories.
                  </p>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* SYSTEM TAB */}
        {activeTab === 'system' && (
          <div className="space-y-6">
            {/* Component Status */}
            <div className="bg-gray-800 rounded-lg border border-gray-700 p-6">
              <h2 className="text-lg font-semibold text-white mb-4">System Components</h2>
              <p className="text-sm text-gray-400 mb-6">
                Monitor the containerized services that power The Metadata Neighborhood. Components are managed by Docker Compose.
              </p>

              <div className="space-y-4">
                {/* API Server */}
                <div className="p-4 bg-gray-900 rounded-lg">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center space-x-3">
                      <div className="w-3 h-3 rounded-full bg-green-400" />
                      <div>
                        <div className="font-medium text-white">API Server</div>
                        <div className="text-sm text-gray-400">
                          Running - Managed by Docker
                        </div>
                      </div>
                    </div>
                    <div className="px-3 py-1 text-xs bg-blue-600/20 text-blue-400 border border-blue-500/30 rounded">
                      Container: cardigan-api
                    </div>
                  </div>
                </div>

                {/* Worker */}
                <div className="p-4 bg-gray-900 rounded-lg">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center space-x-3">
                      <div className={`w-3 h-3 rounded-full ${systemStatus?.worker.running ? 'bg-green-400' : 'bg-yellow-400'}`} />
                      <div>
                        <div className="font-medium text-white">Worker</div>
                        <div className="text-sm text-gray-400">
                          {systemStatus?.worker.running
                            ? 'Running - Managed by Docker'
                            : 'Managed by Docker'}
                        </div>
                      </div>
                    </div>
                    <div className="px-3 py-1 text-xs bg-blue-600/20 text-blue-400 border border-blue-500/30 rounded">
                      Container: cardigan-api
                    </div>
                  </div>
                </div>

                {/* Watcher */}
                <div className="p-4 bg-gray-900 rounded-lg">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center space-x-3">
                      <div className={`w-3 h-3 rounded-full ${systemStatus?.watcher.running ? 'bg-green-400' : 'bg-yellow-400'}`} />
                      <div>
                        <div className="font-medium text-white">Transcript Watcher</div>
                        <div className="text-sm text-gray-400">
                          {systemStatus?.watcher.running
                            ? 'Running - Managed by Docker'
                            : 'Managed by Docker'}
                        </div>
                      </div>
                    </div>
                    <div className="px-3 py-1 text-xs bg-blue-600/20 text-blue-400 border border-blue-500/30 rounded">
                      Container: cardigan-api
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {/* Folder Paths */}
            <div className="bg-gray-800 rounded-lg border border-gray-700 p-6">
              <h2 className="text-lg font-semibold text-white mb-4">Docker Volume Mounts</h2>
              <p className="text-sm text-gray-400 mb-4">
                Persistent data volumes mounted in the container
              </p>
              <div className="space-y-3 text-sm">
                <div className="flex justify-between p-3 bg-gray-900 rounded">
                  <span className="text-gray-400">Transcripts (input)</span>
                  <code className="text-cyan-400">/data/transcripts</code>
                </div>
                <div className="flex justify-between p-3 bg-gray-900 rounded">
                  <span className="text-gray-400">Output (processed)</span>
                  <code className="text-cyan-400">/data/output</code>
                </div>
                <div className="flex justify-between p-3 bg-gray-900 rounded">
                  <span className="text-gray-400">Database</span>
                  <code className="text-cyan-400">/data/db/dashboard.db</code>
                </div>
                <div className="flex justify-between p-3 bg-gray-900 rounded">
                  <span className="text-gray-400">Uploads</span>
                  <code className="text-cyan-400">/data/uploads</code>
                </div>
              </div>
            </div>

            {/* Terminal Commands */}
            <div className="bg-gray-800 rounded-lg border border-gray-700 p-4">
              <div className="flex items-start space-x-3">
                <span className="text-blue-400 text-xl">🐳</span>
                <div className="w-full">
                  <h3 className="text-sm font-medium text-white">Docker Commands</h3>
                  <p className="text-xs text-gray-400 mt-1 mb-3">
                    Manage the containerized system via Docker Compose
                  </p>
                  <div className="space-y-2 text-xs">
                    <div className="p-2 bg-gray-900 rounded">
                      <div className="text-gray-500 mb-1">Restart all services:</div>
                      <code className="text-green-400">docker compose restart</code>
                    </div>
                    <div className="p-2 bg-gray-900 rounded">
                      <div className="text-gray-500 mb-1">View logs:</div>
                      <code className="text-green-400">docker compose logs -f</code>
                    </div>
                    <div className="p-2 bg-gray-900 rounded">
                      <div className="text-gray-500 mb-1">Stop system:</div>
                      <code className="text-green-400">docker compose down</code>
                    </div>
                    <div className="p-2 bg-gray-900 rounded">
                      <div className="text-gray-500 mb-1">Rebuild and restart:</div>
                      <code className="text-green-400">docker compose up --build -d</code>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* ACCESSIBILITY TAB */}
        {activeTab === 'accessibility' && (
          <div className="space-y-6">
            {/* Reduce Motion */}
            <div className="bg-gray-800 rounded-lg border border-gray-700 p-6">
              <h2 className="text-lg font-semibold text-white mb-4">Motion Preferences</h2>
              <p className="text-sm text-gray-400 mb-6">
                Control animations and transitions throughout the dashboard.
              </p>

              <div className="flex items-center justify-between p-4 bg-gray-900 rounded-lg">
                <div>
                  <div className="font-medium text-white">Reduce Motion</div>
                  <div className="text-sm text-gray-400">Minimize or disable animations</div>
                </div>
                <Toggle
                  checked={preferences.reduceMotion}
                  onChange={() => updatePreferences({ reduceMotion: !preferences.reduceMotion })}
                  label="Reduce motion"
                />
              </div>
            </div>

            {/* Text Size */}
            <div className="bg-gray-800 rounded-lg border border-gray-700 p-6">
              <h2 className="text-lg font-semibold text-white mb-4">Text Size</h2>
              <p className="text-sm text-gray-400 mb-6">
                Adjust the base text size for improved readability.
              </p>

              <div className="space-y-3">
                {(['default', 'large', 'larger'] as TextSize[]).map((size) => (
                  <button
                    key={size}
                    onClick={() => updatePreferences({ textSize: size })}
                    className={`w-full p-4 rounded-lg border text-left transition-colors ${
                      preferences.textSize === size
                        ? 'bg-blue-900/20 border-blue-500/30 text-blue-400'
                        : 'bg-gray-900 border-gray-700 text-gray-300 hover:bg-gray-800'
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <div>
                        <div className="font-medium capitalize">{size}</div>
                        <div className="text-sm text-gray-400">
                          {size === 'default' && 'Standard text size (16px base)'}
                          {size === 'large' && 'Larger text size (18px base)'}
                          {size === 'larger' && 'Largest text size (20px base)'}
                        </div>
                      </div>
                      {preferences.textSize === size && (
                        <span className="text-blue-400">✓</span>
                      )}
                    </div>
                    <div className="mt-2 text-gray-400" style={{
                      fontSize: size === 'default' ? '16px' : size === 'large' ? '18px' : '20px'
                    }}>
                      Sample preview text
                    </div>
                  </button>
                ))}
              </div>
            </div>

            {/* High Contrast */}
            <div className="bg-gray-800 rounded-lg border border-gray-700 p-6">
              <h2 className="text-lg font-semibold text-white mb-4">Contrast</h2>
              <p className="text-sm text-gray-400 mb-6">
                Increase contrast between text and backgrounds for better visibility.
              </p>

              <div className="flex items-center justify-between p-4 bg-gray-900 rounded-lg">
                <div>
                  <div className="font-medium text-white">High Contrast Mode</div>
                  <div className="text-sm text-gray-400">Enhance text and UI element contrast</div>
                </div>
                <Toggle
                  checked={preferences.highContrast}
                  onChange={() => updatePreferences({ highContrast: !preferences.highContrast })}
                  label="High contrast mode"
                />
              </div>
            </div>

            {/* Preview Note */}
            <div className="bg-gray-800 rounded-lg border border-gray-700 p-4">
              <div className="flex items-start space-x-3">
                <span className="text-blue-400 text-xl">💡</span>
                <div>
                  <h3 className="text-sm font-medium text-white">Live Preview</h3>
                  <p className="text-xs text-gray-400 mt-1">
                    Changes are applied immediately and saved automatically. Navigate to other pages
                    to see how preferences affect the entire dashboard.
                  </p>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
