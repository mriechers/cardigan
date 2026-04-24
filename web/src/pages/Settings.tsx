import { useEffect, useState, useCallback, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { usePreferences, TextSize } from '../context/PreferencesContext'
import { AGENT_INFO } from '../constants/agents'
import Button from '../components/ui/Button'

function TabIcon({ d }: { d: string }) {
  return (
    <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" d={d} />
    </svg>
  )
}

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

const TIER_STYLES: Record<number, { bg: string; border: string; text: string }> = {
  0: { bg: 'bg-status-completed/10', border: 'border-status-completed/30', text: 'text-status-completed' },
  1: { bg: 'bg-pbs-500/10', border: 'border-pbs-500/30', text: 'text-pbs-400' },
  2: { bg: 'bg-pbs-300/10', border: 'border-pbs-300/30', text: 'text-pbs-300' },
}

type TabId = 'agents' | 'routing' | 'worker' | 'ingest' | 'system' | 'accessibility'

const TABS: { id: TabId; label: string; icon: ReactNode }[] = [
  { id: 'agents', label: 'Agents', icon: <TabIcon d="M9.75 3.104v5.714a2.25 2.25 0 01-.659 1.591L5 14.5M9.75 3.104c-.251.023-.501.05-.75.082m.75-.082a24.301 24.301 0 014.5 0m0 0v5.714c0 .597.237 1.17.659 1.591L19.8 15.3M14.25 3.104c.251.023.501.05.75.082M19.8 15.3l-1.57.393A9.065 9.065 0 0112 15a9.065 9.065 0 00-6.23.693L5 14.5m14.8.8l1.402 1.402c1.232 1.232.65 3.318-1.067 3.611A48.309 48.309 0 0112 21c-2.773 0-5.491-.235-8.135-.687-1.718-.293-2.3-2.379-1.067-3.61L5 14.5" /> },
  { id: 'routing', label: 'Routing', icon: <TabIcon d="M7.5 21L3 16.5m0 0L7.5 12M3 16.5h13.5m0-13.5L21 7.5m0 0L16.5 12M21 7.5H7.5" /> },
  { id: 'worker', label: 'Worker', icon: <TabIcon d="M10.343 3.94c.09-.542.56-.94 1.11-.94h1.093c.55 0 1.02.398 1.11.94l.149.894c.07.424.384.764.78.93.398.164.855.142 1.205-.108l.737-.527a1.125 1.125 0 011.45.12l.773.774c.39.389.44 1.002.12 1.45l-.527.737c-.25.35-.272.806-.107 1.204.165.397.505.71.93.78l.893.15c.543.09.94.56.94 1.109v1.094c0 .55-.397 1.02-.94 1.11l-.893.149c-.425.07-.765.383-.93.78-.165.398-.143.854.107 1.204l.527.738c.32.447.269 1.06-.12 1.45l-.774.773a1.125 1.125 0 01-1.449.12l-.738-.527c-.35-.25-.806-.272-1.203-.107-.397.165-.71.505-.781.929l-.149.894c-.09.542-.56.94-1.11.94h-1.094c-.55 0-1.019-.398-1.11-.94l-.148-.894c-.071-.424-.384-.764-.781-.93-.398-.164-.854-.142-1.204.108l-.738.527c-.447.32-1.06.269-1.45-.12l-.773-.774a1.125 1.125 0 01-.12-1.45l.527-.737c.25-.35.273-.806.108-1.204-.165-.397-.505-.71-.93-.78l-.894-.15c-.542-.09-.94-.56-.94-1.109v-1.094c0-.55.398-1.02.94-1.11l.894-.149c.424-.07.765-.383.93-.78.165-.398.143-.854-.107-1.204l-.527-.738a1.125 1.125 0 01.12-1.45l.773-.773a1.125 1.125 0 011.45-.12l.737.527c.35.25.807.272 1.204.107.397-.165.71-.505.78-.929l.15-.894z" /> },
  { id: 'ingest', label: 'Ingest', icon: <TabIcon d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3" /> },
  { id: 'system', label: 'System', icon: <TabIcon d="M9 17.25v1.007a3 3 0 01-.879 2.122L7.5 21h9l-.621-.621A3 3 0 0115 18.257V17.25m6-12V15a2.25 2.25 0 01-2.25 2.25H5.25A2.25 2.25 0 013 15V5.25m18 0A2.25 2.25 0 0018.75 3H5.25A2.25 2.25 0 003 5.25m18 0V12a2.25 2.25 0 01-2.25 2.25H5.25A2.25 2.25 0 013 12V5.25" /> },
  { id: 'accessibility', label: 'Accessibility', icon: <TabIcon d="M15 19.128a9.38 9.38 0 002.625.372 9.337 9.337 0 004.121-.952 4.125 4.125 0 00-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 018.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0111.964-3.07M12 6.375a3.375 3.375 0 11-6.75 0 3.375 3.375 0 016.75 0zm8.25 2.25a2.625 2.625 0 11-5.25 0 2.625 2.625 0 015.25 0z" /> },
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

      setSuccess('Settings saved. Changes take effect within a few seconds.')
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
      <div className="space-y-8">
        <h1 className="text-2xl font-display font-bold text-white">Settings</h1>
        <div className="bg-surface-800 rounded-lg border border-surface-700 p-6">
          <p className="text-surface-400 animate-pulse">Loading configuration...</p>
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
        checked ? 'bg-pbs-500' : 'bg-surface-600'
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
    <div className="space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-display font-bold text-white">Settings</h1>
        {hasChanges && (
          <div className="flex items-center space-x-3">
            <Button
              variant="ghost"
              onClick={handleReset}
            >
              Reset
            </Button>
            <Button
              variant="primary"
              onClick={handleSave}
              disabled={saving}
            >
              {saving ? 'Saving...' : 'Save Changes'}
            </Button>
          </div>
        )}
      </div>

      {/* Status Messages */}
      {error && (
        <div role="alert" aria-live="assertive" className="bg-status-failed/15 border border-status-failed/30 rounded-lg p-4">
          <p className="text-status-failed">{error}</p>
        </div>
      )}
      {success && (
        <div role="status" aria-live="polite" className="bg-status-completed/15 border border-status-completed/30 rounded-lg p-4">
          <p className="text-status-completed">{success}</p>
        </div>
      )}

      {/* Tab Navigation */}
      <div className="border-b border-surface-700">
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
                  ? 'bg-surface-800 text-white border-b-2 border-pbs-500'
                  : 'text-surface-400 hover:text-surface-200 hover:bg-surface-800/50'
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
          <div className="space-y-5">
            {/* Agent Base Tier Assignment */}
            <div className="bg-surface-800 rounded-lg border border-surface-700 p-6">
              <h2 className="text-lg font-semibold text-white mb-4">Agent Base Tiers</h2>
              <p className="text-sm text-surface-400 mb-6">
                Set the starting tier for each agent. Short transcripts will use this tier.
                Longer transcripts may automatically escalate based on duration thresholds.
              </p>

              <div className="space-y-4">
                {AGENT_INFO.map((agent) => {
                  const currentTier = getCurrentPhaseBaseTier(agent.id)
                  const styles = TIER_STYLES[currentTier] || TIER_STYLES[0]

                  return (
                    <div key={agent.id} className="flex items-center justify-between p-4 bg-surface-900 rounded-lg">
                      <div className="flex items-center space-x-4">
                        <div className="w-10 h-10 rounded-full bg-surface-800 flex items-center justify-center text-lg">
                          {agent.icon}
                        </div>
                        <div>
                          <div className="font-medium text-white">{agent.name}</div>
                          <div className="text-sm text-surface-400">{agent.description}</div>
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
                          <option key={idx} value={idx} className="bg-surface-800 text-white">
                            {label}
                          </option>
                        ))}
                      </select>
                    </div>
                  )
                })}
              </div>

              <div className="mt-4 flex items-center space-x-6 text-xs text-surface-400">
                {routing?.tier_labels?.map((label, idx) => (
                  <div key={idx} className="flex items-center space-x-2">
                    <span className={`w-2 h-2 rounded-full ${
                      idx === 0 ? 'bg-status-completed' :
                      idx === 1 ? 'bg-pbs-500' :
                      'bg-pbs-300'
                    }`} />
                    <span>{label}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* ROUTING TAB */}
        {activeTab === 'routing' && (
          <div className="space-y-5">
            {/* Duration-Based Tier Escalation */}
            <div className="bg-surface-800 rounded-lg border border-surface-700 p-6">
              <h2 className="text-lg font-semibold text-white mb-4">Duration-Based Tier Selection</h2>
              <p className="text-sm text-surface-400 mb-6">
                Set duration thresholds for automatic tier escalation. Longer transcripts require
                more capable models.
              </p>

              <div className="space-y-4">
                {getCurrentThresholds().map((threshold, idx) => {
                  const label = getTierLabel(threshold.tier)
                  const styles = TIER_STYLES[threshold.tier] || TIER_STYLES[0]

                  return (
                    <div key={idx} className={`p-4 rounded-lg border ${styles.bg} ${styles.border}`}>
                      <div className="flex items-center justify-between mb-2">
                        <span className={`font-medium ${styles.text}`}>
                          Tier {threshold.tier}: {label}
                        </span>
                        <span className="text-surface-400 text-sm">
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
                            className="w-full h-2 bg-surface-700 rounded-lg appearance-none cursor-pointer"
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
            <div className="bg-surface-800 rounded-lg border border-surface-700 p-6">
              <h2 className="text-lg font-semibold text-white mb-4">Failure-Based Escalation</h2>
              <p className="text-sm text-surface-400 mb-6">
                When a model fails or times out, automatically retry with the next tier up.
              </p>

              <div className="space-y-4">
                <div className="flex items-center justify-between p-4 bg-surface-900 rounded-lg">
                  <div>
                    <div className="font-medium text-white">Enable Auto-Escalation</div>
                    <div className="text-sm text-surface-400">Automatically retry with higher tiers on failure</div>
                  </div>
                  <Toggle
                    checked={getCurrentEscalation().enabled}
                    onChange={() => handleEscalationChange('enabled', !getCurrentEscalation().enabled)}
                    label="Enable auto-escalation"
                  />
                </div>

                {getCurrentEscalation().enabled && (
                  <>
                    <div className="flex items-center justify-between p-4 bg-surface-900 rounded-lg">
                      <div>
                        <div className="font-medium text-white">Escalate on Failure</div>
                        <div className="text-sm text-surface-400">Retry with next tier when LLM returns an error</div>
                      </div>
                      <Toggle
                        checked={getCurrentEscalation().on_failure}
                        onChange={() => handleEscalationChange('on_failure', !getCurrentEscalation().on_failure)}
                        label="Escalate on failure"
                      />
                    </div>

                    <div className="flex items-center justify-between p-4 bg-surface-900 rounded-lg">
                      <div>
                        <div className="font-medium text-white">Escalate on Timeout</div>
                        <div className="text-sm text-surface-400">Retry with next tier when request times out</div>
                      </div>
                      <Toggle
                        checked={getCurrentEscalation().on_timeout}
                        onChange={() => handleEscalationChange('on_timeout', !getCurrentEscalation().on_timeout)}
                        label="Escalate on timeout"
                      />
                    </div>

                    <div className="p-4 bg-surface-900 rounded-lg">
                      <div className="flex items-center justify-between mb-2">
                        <label htmlFor="timeout-duration" className="font-medium text-white">Timeout Duration</label>
                        <span className="text-surface-400">{getCurrentEscalation().timeout_seconds} seconds</span>
                      </div>
                      <input
                        id="timeout-duration"
                        type="range"
                        min="30"
                        max="300"
                        step="30"
                        value={getCurrentEscalation().timeout_seconds}
                        onChange={(e) => handleEscalationChange('timeout_seconds', parseInt(e.target.value))}
                        className="w-full h-2 bg-surface-700 rounded-lg appearance-none cursor-pointer"
                        aria-valuemin={30}
                        aria-valuemax={300}
                        aria-valuenow={getCurrentEscalation().timeout_seconds}
                      />
                      <div className="flex justify-between text-xs text-surface-400 mt-1">
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
            <div className="bg-surface-800 rounded-lg border border-surface-700 p-4">
              <div className="flex items-start space-x-3">
                <span className="text-yellow-400 text-xl">💡</span>
                <div>
                  <h3 className="text-sm font-medium text-white">Managing OpenRouter Presets</h3>
                  <p className="text-xs text-surface-400 mt-1">
                    These presets are configured in your OpenRouter account. To modify the models in each
                    preset tier, visit{' '}
                    <a
                      href="https://openrouter.ai/settings/presets"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-pbs-400 hover:text-pbs-300"
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
          <div className="space-y-5">
            <div className="bg-surface-800 rounded-lg border border-surface-700 p-6">
              <h2 className="text-lg font-semibold text-white mb-4">Worker Settings</h2>
              <p className="text-sm text-surface-400 mb-6">
                Configure job processing concurrency. Changes require worker restart.
              </p>

              <div className="space-y-4">
                {/* Concurrent Jobs */}
                <div className="p-4 bg-surface-900 rounded-lg">
                  <div className="flex items-center justify-between mb-2">
                    <div>
                      <label htmlFor="concurrent-jobs" className="font-medium text-white">Concurrent Jobs</label>
                      <div className="text-sm text-surface-400">Process multiple jobs simultaneously</div>
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
                    className="w-full h-2 bg-surface-700 rounded-lg appearance-none cursor-pointer"
                    aria-valuemin={1}
                    aria-valuemax={5}
                    aria-valuenow={getCurrentWorker().max_concurrent_jobs}
                  />
                  <div className="flex justify-between text-xs text-surface-400 mt-1">
                    <span>1 (safe)</span>
                    <span>3 (default)</span>
                    <span>5 (max)</span>
                  </div>
                </div>

                {/* Poll Interval */}
                <div className="p-4 bg-surface-900 rounded-lg">
                  <div className="flex items-center justify-between mb-2">
                    <div>
                      <label htmlFor="poll-interval" className="font-medium text-white">Poll Interval</label>
                      <div className="text-sm text-surface-400">Seconds between queue checks</div>
                    </div>
                    <span className="text-surface-300">{getCurrentWorker().poll_interval_seconds}s</span>
                  </div>
                  <input
                    id="poll-interval"
                    type="range"
                    min="1"
                    max="30"
                    step="1"
                    value={getCurrentWorker().poll_interval_seconds}
                    onChange={(e) => handleWorkerChange('poll_interval_seconds', parseInt(e.target.value))}
                    className="w-full h-2 bg-surface-700 rounded-lg appearance-none cursor-pointer"
                    aria-valuemin={1}
                    aria-valuemax={30}
                    aria-valuenow={getCurrentWorker().poll_interval_seconds}
                  />
                  <div className="flex justify-between text-xs text-surface-400 mt-1">
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
          <div className="space-y-5">
            <div className="bg-surface-800 rounded-lg border border-surface-700 p-6">
              <h2 className="text-lg font-semibold text-white mb-4">Ingest Scanner</h2>
              <p className="text-sm text-surface-400 mb-6">
                Automatically scan network locations for new transcript files and add them to the processing queue.
              </p>

              <div className="space-y-4">
                {/* Enable Toggle */}
                <div className="flex items-center justify-between p-4 bg-surface-900 rounded-lg">
                  <div>
                    <div className="font-medium text-white">Enable Scanner</div>
                    <div className="text-sm text-surface-400">Automatically discover and ingest new transcripts</div>
                  </div>
                  <Toggle
                    checked={getCurrentIngest().enabled}
                    onChange={() => handleIngestChange('enabled', !getCurrentIngest().enabled)}
                    label="Enable ingest scanner"
                  />
                </div>

                {/* Scan Interval */}
                <div className="p-4 bg-surface-900 rounded-lg">
                  <div className="flex items-center justify-between mb-2">
                    <div>
                      <label htmlFor="scan-interval" className="font-medium text-white">Scan Interval</label>
                      <div className="text-sm text-surface-400">Hours between automatic scans</div>
                    </div>
                    <span className="text-surface-300">{getCurrentIngest().scan_interval_hours}h</span>
                  </div>
                  <input
                    id="scan-interval"
                    type="range"
                    min="1"
                    max="168"
                    step="1"
                    value={getCurrentIngest().scan_interval_hours}
                    onChange={(e) => handleIngestChange('scan_interval_hours', parseInt(e.target.value))}
                    className="w-full h-2 bg-surface-700 rounded-lg appearance-none cursor-pointer"
                    aria-valuemin={1}
                    aria-valuemax={168}
                    aria-valuenow={getCurrentIngest().scan_interval_hours}
                  />
                  <div className="flex justify-between text-xs text-surface-400 mt-1">
                    <span>1h</span>
                    <span>24h (daily)</span>
                    <span>168h (weekly)</span>
                  </div>
                </div>

                {/* Scan Time */}
                <div className="p-4 bg-surface-900 rounded-lg">
                  <div className="mb-2">
                    <label htmlFor="scan-time" className="font-medium text-white block">Preferred Scan Time</label>
                    <div className="text-sm text-surface-400">Daily time to run scheduled scans (24-hour format)</div>
                  </div>
                  <input
                    id="scan-time"
                    type="time"
                    value={getCurrentIngest().scan_time}
                    onChange={(e) => handleIngestChange('scan_time', e.target.value)}
                    className="px-3 py-2 bg-surface-800 border border-surface-600 rounded-md text-white focus:border-pbs-500 focus:outline-none"
                  />
                </div>

                {/* Server URL (read-only) */}
                <div className="p-4 bg-surface-900 rounded-lg">
                  <div className="mb-2">
                    <div className="font-medium text-white">Server URL</div>
                    <div className="text-sm text-surface-400">Remote file server location</div>
                  </div>
                  <code className="block p-2 bg-surface-800 rounded text-cyan-400 text-sm">
                    {getCurrentIngest().server_url || 'Not configured'}
                  </code>
                </div>

                {/* Last Scan Info */}
                {getCurrentIngest().last_scan_at && (
                  <div className="p-4 bg-surface-900 rounded-lg">
                    <div className="flex items-center justify-between mb-2">
                      <div className="font-medium text-white">Last Scan</div>
                      <span className={`px-2 py-1 rounded text-xs font-medium ${
                        getCurrentIngest().last_scan_success
                          ? 'bg-status-completed/15 text-status-completed'
                          : 'bg-status-failed/15 text-status-failed'
                      }`}>
                        {getCurrentIngest().last_scan_success ? 'Success' : 'Failed'}
                      </span>
                    </div>
                    <div className="text-sm text-surface-400">
                      {formatDateTime(getCurrentIngest().last_scan_at)}
                    </div>
                  </div>
                )}

                {/* Next Scheduled Scan */}
                {getCurrentIngest().next_scan_at && (
                  <div className="p-4 bg-surface-900 rounded-lg">
                    <div className="font-medium text-white mb-2">Next Scheduled Scan</div>
                    <div className="text-sm text-surface-400">
                      {formatDateTime(getCurrentIngest().next_scan_at)}
                    </div>
                  </div>
                )}
              </div>
            </div>

            {/* Link to Ready for Work page */}
            <div className="bg-surface-800 rounded-lg border border-surface-700 p-6">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="text-lg font-medium text-white">Ready for Work</h3>
                  <p className="text-sm text-surface-400 mt-1">
                    View and queue transcripts from the ingest server with search and filtering.
                  </p>
                </div>
                <Link
                  to="/ready"
                  className="px-4 py-2 bg-pbs-500 hover:bg-pbs-400 text-white rounded-lg transition-colors font-medium"
                >
                  Open Ready for Work
                </Link>
              </div>
            </div>

            {/* Screengrab Info */}
            <div className="bg-surface-800 rounded-lg border border-surface-700 p-6">
              <div className="flex items-start space-x-3">
                <div className="flex-shrink-0 w-10 h-10 bg-purple-500/20 rounded-full flex items-center justify-center">
                  <svg className="w-5 h-5 text-purple-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
                  </svg>
                </div>
                <div>
                  <h3 className="text-lg font-medium text-white">Screengrab Attachments</h3>
                  <p className="text-sm text-surface-400 mt-1">
                    Screengrabs are now attached contextually from the job detail page. When a completed job has matching screengrabs available, you'll see an "Attach Screengrabs" button in the job header.
                  </p>
                </div>
              </div>
            </div>

            {/* Configuration Note */}
            <div className="bg-surface-800 rounded-lg border border-surface-700 p-4">
              <div className="flex items-start space-x-3">
                <span className="text-yellow-400 text-xl">💡</span>
                <div>
                  <h3 className="text-sm font-medium text-white">Server Configuration</h3>
                  <p className="text-xs text-surface-400 mt-1">
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
          <div className="space-y-5">
            {/* Component Status */}
            <div className="bg-surface-800 rounded-lg border border-surface-700 p-6">
              <h2 className="text-lg font-semibold text-white mb-4">System Components</h2>
              <p className="text-sm text-surface-400 mb-6">
                Monitor the containerized services that power The Metadata Neighborhood. Components are managed by Docker Compose.
              </p>

              <div className="space-y-4">
                {/* API Server */}
                <div className="p-4 bg-surface-900 rounded-lg">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center space-x-3">
                      <div className="w-3 h-3 rounded-full bg-status-completed" />
                      <div>
                        <div className="font-medium text-white">API Server</div>
                        <div className="text-sm text-surface-400">
                          Running - Managed by Docker
                        </div>
                      </div>
                    </div>
                    <div className="px-3 py-1 text-xs bg-pbs-500/20 text-pbs-400 border border-pbs-500/30 rounded">
                      Container: cardigan-api
                    </div>
                  </div>
                </div>

                {/* Worker */}
                <div className="p-4 bg-surface-900 rounded-lg">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center space-x-3">
                      <div className={`w-3 h-3 rounded-full ${systemStatus?.worker.running ? 'bg-status-completed' : 'bg-status-pending'}`} />
                      <div>
                        <div className="font-medium text-white">Worker</div>
                        <div className="text-sm text-surface-400">
                          {systemStatus?.worker.running
                            ? 'Running - Managed by Docker'
                            : 'Managed by Docker'}
                        </div>
                      </div>
                    </div>
                    <div className="px-3 py-1 text-xs bg-pbs-500/20 text-pbs-400 border border-pbs-500/30 rounded">
                      Container: cardigan-api
                    </div>
                  </div>
                </div>

                {/* Watcher */}
                <div className="p-4 bg-surface-900 rounded-lg">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center space-x-3">
                      <div className={`w-3 h-3 rounded-full ${systemStatus?.watcher.running ? 'bg-status-completed' : 'bg-status-pending'}`} />
                      <div>
                        <div className="font-medium text-white">Transcript Watcher</div>
                        <div className="text-sm text-surface-400">
                          {systemStatus?.watcher.running
                            ? 'Running - Managed by Docker'
                            : 'Managed by Docker'}
                        </div>
                      </div>
                    </div>
                    <div className="px-3 py-1 text-xs bg-pbs-500/20 text-pbs-400 border border-pbs-500/30 rounded">
                      Container: cardigan-api
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {/* Folder Paths */}
            <div className="bg-surface-800 rounded-lg border border-surface-700 p-6">
              <h2 className="text-lg font-semibold text-white mb-4">Docker Volume Mounts</h2>
              <p className="text-sm text-surface-400 mb-4">
                Persistent data volumes mounted in the container
              </p>
              <div className="space-y-3 text-sm">
                <div className="flex justify-between p-3 bg-surface-900 rounded">
                  <span className="text-surface-400">Transcripts (input)</span>
                  <code className="text-cyan-400">/data/transcripts</code>
                </div>
                <div className="flex justify-between p-3 bg-surface-900 rounded">
                  <span className="text-surface-400">Output (processed)</span>
                  <code className="text-cyan-400">/data/output</code>
                </div>
                <div className="flex justify-between p-3 bg-surface-900 rounded">
                  <span className="text-surface-400">Database</span>
                  <code className="text-cyan-400">/data/db/dashboard.db</code>
                </div>
                <div className="flex justify-between p-3 bg-surface-900 rounded">
                  <span className="text-surface-400">Uploads</span>
                  <code className="text-cyan-400">/data/uploads</code>
                </div>
              </div>
            </div>

            {/* Terminal Commands */}
            <div className="bg-surface-800 rounded-lg border border-surface-700 p-4">
              <div className="flex items-start space-x-3">
                <span className="text-pbs-400 text-xl">🐳</span>
                <div className="w-full">
                  <h3 className="text-sm font-medium text-white">Docker Commands</h3>
                  <p className="text-xs text-surface-400 mt-1 mb-3">
                    Manage the containerized system via Docker Compose
                  </p>
                  <div className="space-y-2 text-xs">
                    <div className="p-2 bg-surface-900 rounded">
                      <div className="text-surface-400 mb-1">Restart all services:</div>
                      <code className="text-green-400">docker compose restart</code>
                    </div>
                    <div className="p-2 bg-surface-900 rounded">
                      <div className="text-surface-400 mb-1">View logs:</div>
                      <code className="text-green-400">docker compose logs -f</code>
                    </div>
                    <div className="p-2 bg-surface-900 rounded">
                      <div className="text-surface-400 mb-1">Stop system:</div>
                      <code className="text-green-400">docker compose down</code>
                    </div>
                    <div className="p-2 bg-surface-900 rounded">
                      <div className="text-surface-400 mb-1">Rebuild and restart:</div>
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
          <div className="space-y-5">
            {/* Reduce Motion */}
            <div className="bg-surface-800 rounded-lg border border-surface-700 p-6">
              <h2 className="text-lg font-semibold text-white mb-4">Motion Preferences</h2>
              <p className="text-sm text-surface-400 mb-6">
                Control animations and transitions throughout the dashboard.
              </p>

              <div className="flex items-center justify-between p-4 bg-surface-900 rounded-lg">
                <div>
                  <div className="font-medium text-white">Reduce Motion</div>
                  <div className="text-sm text-surface-400">Minimize or disable animations</div>
                </div>
                <Toggle
                  checked={preferences.reduceMotion}
                  onChange={() => updatePreferences({ reduceMotion: !preferences.reduceMotion })}
                  label="Reduce motion"
                />
              </div>
            </div>

            {/* Text Size */}
            <div className="bg-surface-800 rounded-lg border border-surface-700 p-6">
              <h2 className="text-lg font-semibold text-white mb-4">Text Size</h2>
              <p className="text-sm text-surface-400 mb-6">
                Adjust the base text size for improved readability.
              </p>

              <div className="space-y-3">
                {(['default', 'large', 'larger'] as TextSize[]).map((size) => (
                  <button
                    key={size}
                    onClick={() => updatePreferences({ textSize: size })}
                    className={`w-full p-4 rounded-lg border text-left transition-colors ${
                      preferences.textSize === size
                        ? 'bg-pbs-900/20 border-pbs-500/30 text-pbs-400'
                        : 'bg-surface-900 border-surface-700 text-surface-300 hover:bg-surface-800'
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <div>
                        <div className="font-medium capitalize">{size}</div>
                        <div className="text-sm text-surface-400">
                          {size === 'default' && 'Standard text size (16px base)'}
                          {size === 'large' && 'Larger text size (18px base)'}
                          {size === 'larger' && 'Largest text size (20px base)'}
                        </div>
                      </div>
                      {preferences.textSize === size && (
                        <span className="text-pbs-400">✓</span>
                      )}
                    </div>
                    <div className="mt-2 text-surface-400" style={{
                      fontSize: size === 'default' ? '16px' : size === 'large' ? '18px' : '20px'
                    }}>
                      Sample preview text
                    </div>
                  </button>
                ))}
              </div>
            </div>

            {/* High Contrast */}
            <div className="bg-surface-800 rounded-lg border border-surface-700 p-6">
              <h2 className="text-lg font-semibold text-white mb-4">Contrast</h2>
              <p className="text-sm text-surface-400 mb-6">
                Increase contrast between text and backgrounds for better visibility.
              </p>

              <div className="flex items-center justify-between p-4 bg-surface-900 rounded-lg">
                <div>
                  <div className="font-medium text-white">High Contrast Mode</div>
                  <div className="text-sm text-surface-400">Enhance text and UI element contrast</div>
                </div>
                <Toggle
                  checked={preferences.highContrast}
                  onChange={() => updatePreferences({ highContrast: !preferences.highContrast })}
                  label="High contrast mode"
                />
              </div>
            </div>

            {/* Preview Note */}
            <div className="bg-surface-800 rounded-lg border border-surface-700 p-4">
              <div className="flex items-start space-x-3">
                <span className="text-pbs-400 text-xl">💡</span>
                <div>
                  <h3 className="text-sm font-medium text-white">Live Preview</h3>
                  <p className="text-xs text-surface-400 mt-1">
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
