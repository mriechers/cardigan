import { useEffect, useState } from 'react'

interface ModelTimelineEntry {
  model: string
  count: number
  cost: number
}

interface ModelTimelineDay {
  date: string
  models: ModelTimelineEntry[]
  primary_model: string | null
  primary_changed: boolean
}

interface ModelTimelineResponse {
  available: boolean
  days: ModelTimelineDay[]
  period_days: number
  all_models: string[]
}

interface ModelTimelineWidgetProps {
  className?: string
}

/**
 * Truncate a full model identifier to just the short model name.
 * "anthropic/claude-sonnet-4-5-20250514" -> "claude-sonnet-4.5"
 * "anthropic/claude-haiku-4-5" -> "claude-haiku-4.5"
 */
function shortModelName(model: string): string {
  // Strip provider prefix
  const withoutProvider = model.includes('/') ? model.split('/').slice(1).join('/') : model
  // Replace trailing date suffix like -20250514
  const withoutDate = withoutProvider.replace(/-\d{8}$/, '')
  // Normalise version separators: -4-5 -> -4.5, -3-5 -> -3.5
  const normalised = withoutDate.replace(/-(\d)-(\d)(?=-|$)/g, '-$1.$2')
  // Trim to 30 chars max
  return normalised.length > 30 ? normalised.slice(0, 27) + '...' : normalised
}

/**
 * Return a Tailwind text-color class based on model tier heuristics.
 * haiku / free  -> green
 * sonnet        -> cyan
 * opus / pro    -> purple
 * others        -> gray
 */
function modelTextColor(model: string): string {
  const lower = model.toLowerCase()
  if (lower.includes('haiku') || lower.includes(':free') || lower.includes('cheapskate')) {
    return 'text-green-400'
  }
  if (lower.includes('opus') || lower.includes('pro')) {
    return 'text-purple-400'
  }
  if (lower.includes('sonnet')) {
    return 'text-cyan-400'
  }
  return 'text-gray-400'
}

/**
 * Widget showing a daily breakdown of which models were used, ordered most-recent first.
 * Highlights rows where the primary model changed from the previous day.
 */
export default function ModelTimelineWidget({ className = '' }: ModelTimelineWidgetProps) {
  const [data, setData] = useState<ModelTimelineResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [days, setDays] = useState(30)

  const fetchTimeline = async () => {
    setLoading(true)
    try {
      const response = await fetch(`/api/langfuse/model-timeline?days=${days}`)
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`)
      }
      const json = await response.json()
      setData(json)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch timeline')
      setData(null)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchTimeline()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [days])

  const formatCost = (cost: number) => {
    if (cost === 0) return ''
    if (cost < 0.01) return `$${cost.toFixed(4)}`
    if (cost < 1) return `$${cost.toFixed(3)}`
    return `$${cost.toFixed(2)}`
  }

  // Rows are displayed most-recent first
  const reversedDays = data ? [...data.days].reverse() : []

  return (
    <div className={`bg-gray-800 rounded-lg border border-gray-700 p-4 ${className}`}>
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div>
          <h3 className="text-sm font-medium text-gray-400 uppercase tracking-wide">
            Model Selection Over Time
          </h3>
          <p className="text-xs text-gray-500 mt-0.5">
            Daily breakdown from local session stats
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
          </select>
          <button
            onClick={fetchTimeline}
            disabled={loading}
            className="text-xs text-gray-400 hover:text-white disabled:opacity-50"
            aria-label="Refresh timeline"
          >
            {loading ? '...' : 'Refresh'}
          </button>
        </div>
      </div>

      {/* Loading skeleton */}
      {loading && !data && (
        <div className="animate-pulse space-y-2">
          <div className="h-6 bg-gray-700 rounded w-full"></div>
          <div className="h-6 bg-gray-700 rounded w-5/6"></div>
          <div className="h-6 bg-gray-700 rounded w-4/6"></div>
        </div>
      )}

      {/* Fetch error */}
      {error && !loading && (
        <div className="text-center py-4">
          <p className="text-red-400 text-sm">{error}</p>
          <button
            onClick={fetchTimeline}
            className="mt-2 text-xs text-blue-400 hover:text-blue-300"
          >
            Try again
          </button>
        </div>
      )}

      {/* Empty state */}
      {data && data.available && reversedDays.length === 0 && (
        <p className="text-gray-500 text-sm text-center py-6">
          No model usage data for the last {days} days
        </p>
      )}

      {/* Timeline table */}
      {data && data.available && reversedDays.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs font-mono">
            <thead>
              <tr className="border-b border-gray-700">
                <th className="text-left text-gray-500 pb-2 pr-4 font-normal whitespace-nowrap">
                  Date
                </th>
                {data.all_models.map((model) => (
                  <th
                    key={model}
                    className={`text-right pb-2 px-2 font-normal whitespace-nowrap ${modelTextColor(model)}`}
                    title={model}
                  >
                    {shortModelName(model)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {reversedDays.map((day) => {
                // Build a quick lookup for this day's model entries
                const modelMap = new Map<string, ModelTimelineEntry>()
                for (const entry of day.models) {
                  modelMap.set(entry.model, entry)
                }

                return (
                  <tr
                    key={day.date}
                    className={
                      day.primary_changed
                        ? 'border-l-2 border-yellow-500/60 bg-yellow-900/5'
                        : 'border-l-2 border-transparent'
                    }
                  >
                    <td className="py-1 pr-4 text-gray-500 whitespace-nowrap align-middle">
                      {day.date}
                      {day.primary_changed && (
                        <span
                          className="ml-1 text-yellow-500/80"
                          title="Primary model changed from previous day"
                        >
                          *
                        </span>
                      )}
                    </td>
                    {data.all_models.map((model) => {
                      const entry = modelMap.get(model)
                      const isPrimary = day.primary_model === model
                      const color = modelTextColor(model)

                      if (!entry) {
                        return (
                          <td key={model} className="py-1 px-2 text-right text-gray-700 align-middle">
                            —
                          </td>
                        )
                      }

                      return (
                        <td
                          key={model}
                          className={`py-1 px-2 text-right align-middle ${color}`}
                          title={formatCost(entry.cost) ? `${entry.count} requests · ${formatCost(entry.cost)}` : `${entry.count} requests`}
                        >
                          {isPrimary ? (
                            <span className="inline-flex items-center gap-1">
                              <span className="text-[9px] uppercase tracking-wide opacity-60">pri</span>
                              {entry.count}
                            </span>
                          ) : (
                            entry.count
                          )}
                        </td>
                      )
                    })}
                  </tr>
                )
              })}
            </tbody>
          </table>
          <p className="text-[10px] text-gray-600 mt-3">
            <span className="text-yellow-500/70">* </span>
            Primary model changed from previous day.
            {' '}
            <span className="text-green-400">green</span> = haiku/free,{' '}
            <span className="text-cyan-400">cyan</span> = sonnet,{' '}
            <span className="text-purple-400">purple</span> = opus/pro.
            Hover a cell for cost.
          </p>
        </div>
      )}
    </div>
  )
}
