import { useEffect, useState } from 'react'
import PhaseStatsWidget from '../components/PhaseStatsWidget'

interface DurationThreshold {
  max_minutes: number | null
  tier: number
}

interface HealthStatus {
  status: string
  queue?: {
    pending: number
    in_progress: number
  }
  llm?: {
    routing?: {
      tier_labels: string[]
      duration_thresholds: DurationThreshold[]
    }
  }
  last_run?: {
    total_cost: number
    total_tokens: number
  } | null
}

interface ConnectionLog {
  timestamp: Date
  success: boolean
  error?: string
  latency?: number
}

export default function System() {
  const [health, setHealth] = useState<HealthStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [checking, setChecking] = useState(false)
  const [logs, setLogs] = useState<ConnectionLog[]>([])

  const checkConnection = async () => {
    setChecking(true)
    const start = Date.now()

    try {
      const response = await fetch('/api/system/health')
      const latency = Date.now() - start

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`)
      }

      const data = await response.json()
      setHealth(data)
      setError(null)
      setLogs(prev => [{
        timestamp: new Date(),
        success: true,
        latency
      }, ...prev.slice(0, 9)])
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : 'Unknown error'
      setError(errorMsg)
      setHealth(null)
      setLogs(prev => [{
        timestamp: new Date(),
        success: false,
        error: errorMsg
      }, ...prev.slice(0, 9)])
    } finally {
      setChecking(false)
    }
  }

  useEffect(() => {
    checkConnection()
  }, [])

  const isConnected = health !== null && error === null

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-white">System Status</h1>

      {/* Connection Status Card */}
      <div role={isConnected ? "status" : "alert"} aria-live={isConnected ? "polite" : "assertive"} className={`rounded-lg border p-6 ${
        isConnected
          ? 'bg-green-900/20 border-green-500/30'
          : 'bg-red-900/20 border-red-500/30'
      }`}>
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-4">
            <div className={`w-4 h-4 rounded-full ${
              isConnected ? 'bg-green-500' : 'bg-red-500 animate-pulse'
            }`} />
            <div>
              <h2 className={`text-xl font-semibold ${
                isConnected ? 'text-green-400' : 'text-red-400'
              }`}>
                {isConnected ? 'API Connected' : 'API Offline'}
              </h2>
              <p className="text-sm text-gray-400">
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
            className="px-4 py-2 bg-gray-700 hover:bg-gray-600 disabled:opacity-50 text-white rounded-md text-sm transition-colors"
          >
            {checking ? 'Checking...' : 'Check Now'}
          </button>
        </div>
      </div>

      {/* Troubleshooting Section - Only show when offline */}
      {!isConnected && (
        <div className="bg-gray-800 rounded-lg border border-gray-700 p-6">
          <h2 className="text-lg font-semibold text-white mb-4">Troubleshooting</h2>

          <div className="space-y-4">
            <div className="flex items-start space-x-3">
              <span className="text-yellow-400 font-mono">1.</span>
              <div>
                <p className="text-white font-medium">Check if the API server is running</p>
                <p className="text-sm text-gray-400 mt-1">
                  The API should be running on port 8100. Open a terminal and run:
                </p>
                <pre className="mt-2 bg-gray-900 rounded p-3 text-sm text-green-400 font-mono overflow-x-auto">
                  lsof -i :8100
                </pre>
              </div>
            </div>

            <div className="flex items-start space-x-3">
              <span className="text-yellow-400 font-mono">2.</span>
              <div>
                <p className="text-white font-medium">Start the API server</p>
                <p className="text-sm text-gray-400 mt-1">
                  Navigate to the project root and start the server:
                </p>
                <pre className="mt-2 bg-gray-900 rounded p-3 text-sm text-green-400 font-mono overflow-x-auto">
{`cd /Users/mriechers/Developer/ai-editorial-assistant-v3
./venv/bin/uvicorn api.main:app --reload --port 8100`}
                </pre>
              </div>
            </div>

            <div className="flex items-start space-x-3">
              <span className="text-yellow-400 font-mono">3.</span>
              <div>
                <p className="text-white font-medium">Check for errors in the API logs</p>
                <p className="text-sm text-gray-400 mt-1">
                  If the server crashed, check the terminal where it was running for error messages.
                  Common issues include database migrations or missing dependencies.
                </p>
              </div>
            </div>

            <div className="flex items-start space-x-3">
              <span className="text-yellow-400 font-mono">4.</span>
              <div>
                <p className="text-white font-medium">Run database migrations</p>
                <p className="text-sm text-gray-400 mt-1">
                  If there are schema errors, run Alembic migrations:
                </p>
                <pre className="mt-2 bg-gray-900 rounded p-3 text-sm text-green-400 font-mono overflow-x-auto">
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
          <div className="bg-gray-800 rounded-lg border border-gray-700 p-4">
            <h3 className="text-sm font-medium text-gray-400 uppercase tracking-wide mb-3">
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

          {health.llm?.routing && (
            <div className="bg-gray-800 rounded-lg border border-gray-700 p-4">
              <h3 className="text-sm font-medium text-gray-400 uppercase tracking-wide mb-3">
                Duration Escalation
              </h3>
              <p className="text-xs text-gray-500 mb-3">
                Base tier is escalated automatically for longer transcripts
              </p>
              <div className="space-y-1.5">
                {health.llm.routing.duration_thresholds.map((threshold, idx) => {
                  const label = health.llm?.routing?.tier_labels?.[threshold.tier] || `Tier ${threshold.tier}`
                  const tierColors = ['text-green-400', 'text-cyan-400', 'text-purple-400']
                  return (
                    <div key={idx} className="flex items-center justify-between text-sm">
                      <span className={tierColors[threshold.tier] || 'text-gray-400'}>
                        {label}
                      </span>
                      <span className="text-gray-500 text-xs">
                        {threshold.max_minutes === null
                          ? '45+ min'
                          : idx === 0
                          ? `≤ ${threshold.max_minutes} min`
                          : `≤ ${threshold.max_minutes} min`}
                      </span>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Agent Performance - Show when connected */}
      {isConnected && (
        <PhaseStatsWidget />
      )}

      {/* Connection Log */}
      <div className="bg-gray-800 rounded-lg border border-gray-700 p-4">
        <h3 className="text-sm font-medium text-gray-400 uppercase tracking-wide mb-3">
          Connection Log
        </h3>
        {logs.length === 0 ? (
          <p className="text-gray-500 text-sm">No connection attempts yet</p>
        ) : (
          <div className="space-y-1">
            {logs.map((log, i) => (
              <div key={i} className="flex items-center space-x-3 text-sm font-mono">
                <span className="text-gray-600">
                  {log.timestamp.toLocaleTimeString()}
                </span>
                <span className={log.success ? 'text-green-400' : 'text-red-400'}>
                  {log.success ? 'OK' : 'FAIL'}
                </span>
                {log.latency && (
                  <span className="text-gray-500">{log.latency}ms</span>
                )}
                {log.error && (
                  <span className="text-red-400 truncate">{log.error}</span>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* API Endpoints Reference */}
      <div className="bg-gray-800 rounded-lg border border-gray-700 p-4">
        <h3 className="text-sm font-medium text-gray-400 uppercase tracking-wide mb-3">
          API Endpoints
        </h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-sm font-mono">
          <div className="text-gray-400">
            <span className="text-blue-400">GET</span> /api/system/health
          </div>
          <div className="text-gray-400">
            <span className="text-green-400">POST</span> /api/queue/
          </div>
          <div className="text-gray-400">
            <span className="text-blue-400">GET</span> /api/queue/
          </div>
          <div className="text-gray-400">
            <span className="text-blue-400">GET</span> /api/queue/stats
          </div>
          <div className="text-gray-400">
            <span className="text-blue-400">GET</span> /api/jobs/:id
          </div>
          <div className="text-gray-400">
            <span className="text-yellow-400">PATCH</span> /api/jobs/:id
          </div>
          <div className="text-gray-400">
            <span className="text-blue-400">GET</span> /api/langfuse/model-stats
          </div>
          <div className="text-gray-400">
            <span className="text-blue-400">GET</span> /api/langfuse/phase-stats
          </div>
          <div className="text-gray-400">
            <span className="text-blue-400">GET</span> /api/langfuse/status
          </div>
        </div>
      </div>
    </div>
  )
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between">
      <span className="text-gray-400">{label}</span>
      <span className="text-white font-mono">{value}</span>
    </div>
  )
}
