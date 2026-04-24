import { useEffect, useState } from 'react'
import ModelStatsWidget from '../components/ModelStatsWidget'
import PhaseStatsWidget from '../components/PhaseStatsWidget'
import { AGENT_INFO } from '../constants/agents'

interface PresetInfo {
  description: string
  models: string[]
  models_verified?: string
}

interface HealthStatus {
  status: string
  queue?: {
    pending: number
    in_progress: number
  }
  llm?: {
    active_model: string | null
    active_backend: string | null
    active_preset: string | null
    primary_backend: string | null
    configured_preset: string | null
    fallback_model: string | null
    phase_backends?: Record<string, string>
    openrouter_presets?: Record<string, PresetInfo>
  }
  last_run?: {
    total_cost: number
    total_tokens: number
  } | null
}

export default function System() {
  const [health, setHealth] = useState<HealthStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [checking, setChecking] = useState(false)

  const checkConnection = async () => {
    setChecking(true)

    try {
      const response = await fetch('/api/system/health')

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`)
      }

      const data = await response.json()
      setHealth(data)
      setError(null)
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : 'Unknown error'
      setError(errorMsg)
      setHealth(null)
    } finally {
      setChecking(false)
    }
  }

  useEffect(() => {
    checkConnection()
  }, [])

  const isConnected = health !== null && error === null

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-bold text-white">System Status</h1>

      {/* Connection Status Card */}
      <div role={isConnected ? "status" : "alert"} aria-live={isConnected ? "polite" : "assertive"} className={`rounded-lg border p-6 ${
        isConnected
          ? 'bg-status-completed/15 border-status-completed/30'
          : 'bg-status-failed/15 border-status-failed/30'
      }`}>
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-4">
            <div className={`w-4 h-4 rounded-full ${
              isConnected ? 'bg-status-completed' : 'bg-status-failed animate-pulse'
            }`} />
            <div>
              <h2 className={`text-xl font-semibold ${
                isConnected ? 'text-status-completed' : 'text-status-failed'
              }`}>
                {isConnected ? 'API Connected' : 'API Offline'}
              </h2>
              <p className="text-sm text-surface-400">
                {isConnected
                  ? 'Backend server is responding normally'
                  : error || 'Unable to reach the API server'
                }
              </p>
            </div>
          </div>
          <button
            onClick={checkConnection}
            disabled={checking}
            className="px-4 py-2 bg-surface-700 hover:bg-surface-600 disabled:opacity-50 text-white rounded-md text-sm transition-colors"
          >
            {checking ? 'Checking...' : 'Check Now'}
          </button>
        </div>
      </div>

      {/* Troubleshooting Section - Only show when offline */}
      {!isConnected && (
        <div className="bg-surface-800 rounded-lg border border-surface-700 p-6">
          <h2 className="text-lg font-semibold text-white mb-4">Troubleshooting</h2>

          <div className="space-y-4">
            <div className="flex items-start space-x-3">
              <span className="text-yellow-400 font-mono">1.</span>
              <div>
                <p className="text-white font-medium">Check if the API server is running</p>
                <p className="text-sm text-surface-400 mt-1">
                  The API should be running on port 8000. Open a terminal and run:
                </p>
                <pre className="mt-2 bg-surface-900 rounded p-3 text-sm text-green-400 font-mono overflow-x-auto">
                  lsof -i :8000
                </pre>
              </div>
            </div>

            <div className="flex items-start space-x-3">
              <span className="text-yellow-400 font-mono">2.</span>
              <div>
                <p className="text-white font-medium">Start the API server</p>
                <p className="text-sm text-surface-400 mt-1">
                  Navigate to the project root and start the server:
                </p>
                <pre className="mt-2 bg-surface-900 rounded p-3 text-sm text-green-400 font-mono overflow-x-auto">
{`cd /Users/mriechers/Developer/ai-editorial-assistant-v3
./venv/bin/uvicorn api.main:app --reload --port 8000`}
                </pre>
              </div>
            </div>

            <div className="flex items-start space-x-3">
              <span className="text-yellow-400 font-mono">3.</span>
              <div>
                <p className="text-white font-medium">Check for errors in the API logs</p>
                <p className="text-sm text-surface-400 mt-1">
                  If the server crashed, check the terminal where it was running for error messages.
                  Common issues include database migrations or missing dependencies.
                </p>
              </div>
            </div>

            <div className="flex items-start space-x-3">
              <span className="text-yellow-400 font-mono">4.</span>
              <div>
                <p className="text-white font-medium">Run database migrations</p>
                <p className="text-sm text-surface-400 mt-1">
                  If there are schema errors, run Alembic migrations:
                </p>
                <pre className="mt-2 bg-surface-900 rounded p-3 text-sm text-green-400 font-mono overflow-x-auto">
                  ./venv/bin/alembic upgrade head
                </pre>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* System Info - Show when connected */}
      {isConnected && health && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="bg-surface-800 rounded-lg border border-surface-700 p-4">
            <h3 className="text-sm font-medium text-surface-400 uppercase tracking-wide mb-3">
              LLM Configuration
            </h3>
            <div className="space-y-2">
              <InfoRow
                label="Primary Backend"
                value={health.llm?.primary_backend || 'Not configured'}
              />
              {health.llm?.configured_preset ? (
                <>
                  <InfoRow
                    label="Model Preset"
                    value={health.llm.configured_preset}
                  />
                  <p className="text-xs text-surface-400 mt-1">
                    OpenRouter selects from preset's model pool
                  </p>
                </>
              ) : (
                <InfoRow
                  label="Fallback Model"
                  value={health.llm?.fallback_model || 'Not configured'}
                />
              )}
              {health.llm?.active_model && (
                <div className="pt-2 border-t border-surface-700 mt-2">
                  <div className="text-xs text-surface-400 mb-1">Currently processing with:</div>
                  <InfoRow
                    label="Active Model"
                    value={health.llm.active_model}
                  />
                </div>
              )}
            </div>
          </div>

          <div className="bg-surface-800 rounded-lg border border-surface-700 p-4">
            <h3 className="text-sm font-medium text-surface-400 uppercase tracking-wide mb-3">
              Queue Status
            </h3>
            <div className="space-y-2">
              <InfoRow label="Pending Jobs" value={String(health.queue?.pending ?? 0)} />
              <InfoRow label="In Progress" value={String(health.queue?.in_progress ?? 0)} />
              {health.last_run && (
                <>
                  <InfoRow label="Last Run Cost" value={`$${health.last_run.total_cost.toFixed(4)}`} />
                  <InfoRow label="Last Run Tokens" value={health.last_run.total_tokens.toLocaleString()} />
                </>
              )}
            </div>
          </div>
        </div>
      )}

      {/* OpenRouter Presets - Show when connected */}
      {isConnected && health?.llm?.openrouter_presets && Object.keys(health.llm.openrouter_presets).length > 0 && (
        <div className="bg-surface-800 rounded-lg border border-surface-700 p-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-medium text-surface-400 uppercase tracking-wide">
              OpenRouter Presets
            </h3>
            <a
              href="https://openrouter.ai/settings/presets"
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-pbs-400 hover:text-pbs-300 flex items-center space-x-1"
            >
              <span>Manage Presets</span>
              <span>↗</span>
            </a>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {Object.entries(health.llm.openrouter_presets).map(([presetName, preset]) => {
              const isBigBrain = presetName.includes('big-brain')
              const isCheapskate = presetName.includes('cheapskate')
              const colorClass = isBigBrain
                ? 'bg-pbs-300/10 border-pbs-300/30'
                : isCheapskate
                ? 'bg-status-completed/10 border-status-completed/30'
                : 'bg-pbs-500/10 border-pbs-500/30'
              const textClass = isBigBrain
                ? 'text-pbs-300'
                : isCheapskate
                ? 'text-status-completed'
                : 'text-pbs-400'
              return (
                <div key={presetName} className={`rounded-lg p-3 border ${colorClass}`}>
                  <div className="flex items-center space-x-2 mb-2">
                    <span className={`text-sm font-medium ${textClass}`}>
                      {presetName.replace('ai-editorial-assistant-', '').replace('ai-editorial-assistant', 'default')}
                    </span>
                  </div>
                  <p className="text-xs text-surface-400 mb-2">{preset.description}</p>
                  <div className="space-y-1">
                    {preset.models.map((model, idx) => (
                      <div key={idx} className="text-xs font-mono text-surface-400 flex items-center space-x-2">
                        <span className="text-surface-600">{idx + 1}.</span>
                        <span>{model}</span>
                      </div>
                    ))}
                  </div>
                  {preset.models_verified && (
                    <p className="text-[10px] text-surface-600 mt-2">
                      Verified {new Date(preset.models_verified + 'T00:00:00').toLocaleDateString()}
                    </p>
                  )}
                </div>
              )
            })}
          </div>
          <p className="text-xs text-surface-600 mt-3 italic">
            Model lists are maintained locally. Update config/llm-config.json when presets change on OpenRouter.
          </p>
        </div>
      )}

      {/* Actual Model Usage from Langfuse - Show when connected */}
      {isConnected && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <ModelStatsWidget />
          <PhaseStatsWidget />
        </div>
      )}

      {/* Agent Roster - Show when connected */}
      {isConnected && health?.llm?.phase_backends && (
        <div className="bg-surface-800 rounded-lg border border-surface-700 p-4">
          <h3 className="text-sm font-medium text-surface-400 uppercase tracking-wide mb-3">
            Agent Roster
          </h3>
          <div className="space-y-3">
            {AGENT_INFO.map((agent) => {
              const backend = health.llm?.phase_backends?.[agent.id] || 'openrouter'
              const isBigBrain = backend.includes('big-brain')
              const isCheapskate = backend.includes('cheapskate')
              const badgeClass = isBigBrain
                ? 'bg-pbs-300/15 text-pbs-300 border border-pbs-300/30'
                : isCheapskate
                ? 'bg-status-completed/15 text-status-completed border border-status-completed/30'
                : 'bg-pbs-500/15 text-pbs-400 border border-pbs-500/30'
              const tierLabel = isBigBrain ? 'big-brain' : isCheapskate ? 'cheapskate' : 'default'
              return (
                <div key={agent.id} className="flex items-start space-x-4 p-3 bg-surface-900 rounded-lg">
                  <div className="flex-shrink-0 w-10 h-10 rounded-full bg-surface-800 flex items-center justify-center text-lg">
                    {agent.icon}
                  </div>
                  <div className="flex-grow min-w-0">
                    <div className="flex items-center space-x-2">
                      <span className="font-medium text-white">{agent.name}</span>
                      <span className={`text-xs px-2 py-0.5 rounded-full ${badgeClass}`}>
                        {tierLabel}
                      </span>
                    </div>
                    <p className="text-sm text-surface-400 mt-1">{agent.description}</p>
                  </div>
                </div>
              )
            })}
          </div>
          <p className="text-xs text-surface-400 mt-3">
            <span className="text-pbs-300">big-brain</span> = complex reasoning |{' '}
            <span className="text-pbs-400">default</span> = balanced |{' '}
            <span className="text-status-completed">cheapskate</span> = free tier
          </p>
        </div>
      )}

    </div>
  )
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between">
      <span className="text-surface-400">{label}</span>
      <span className="text-white font-mono">{value}</span>
    </div>
  )
}
