import { useEffect, useRef, useCallback, useState } from 'react'

interface Job {
  id: number
  project_name: string
  transcript_file: string
  status: string
  priority: number
  queued_at: string
  current_phase: string | null
  [key: string]: unknown // Allow other job fields
}

interface QueueStats {
  pending: number
  in_progress: number
  completed: number
  failed: number
  cancelled: number
  paused: number
  total: number
}

interface WebSocketMessage {
  type: 'job_created' | 'job_updated' | 'job_started' | 'job_completed' | 'job_failed' | 'stats_updated'
  job?: Job
  stats?: QueueStats
}

interface UseJobsWebSocketOptions {
  onJobUpdate?: (job: Job, eventType: string) => void
  onStatsUpdate?: (stats: QueueStats) => void
  autoReconnect?: boolean
  reconnectInterval?: number
}

interface UseJobsWebSocketReturn {
  isConnected: boolean
  lastMessage: WebSocketMessage | null
  connectionError: Error | null
}

/**
 * React hook for WebSocket connection to job updates.
 *
 * Connects to the API's WebSocket endpoint and receives real-time job updates.
 * Automatically reconnects on disconnect and handles connection lifecycle.
 *
 * @param options - Configuration options for the WebSocket connection
 * @returns Connection state and last received message
 *
 * @example
 * ```tsx
 * const { isConnected, lastMessage } = useJobsWebSocket({
 *   onJobUpdate: (job, eventType) => {
 *     console.log(`Job ${job.id} ${eventType}`)
 *     // Update local state
 *   },
 *   onStatsUpdate: (stats) => {
 *     console.log('Queue stats updated:', stats)
 *   }
 * })
 * ```
 */
export function useJobsWebSocket(options: UseJobsWebSocketOptions = {}): UseJobsWebSocketReturn {
  const {
    onJobUpdate,
    onStatsUpdate,
    autoReconnect = true,
    reconnectInterval = 3000,
  } = options

  const [isConnected, setIsConnected] = useState(false)
  const [lastMessage, setLastMessage] = useState<WebSocketMessage | null>(null)
  const [connectionError, setConnectionError] = useState<Error | null>(null)

  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const heartbeatIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const isIntentionallyClosed = useRef(false)

  const connect = useCallback(() => {
    // Don't connect if already connected or intentionally closed
    if (wsRef.current?.readyState === WebSocket.OPEN || isIntentionallyClosed.current) {
      return
    }

    try {
      // Determine WebSocket URL based on current location
      // Uses window.location.host (includes port when non-standard) so
      // WebSocket routes through Vite's proxy in dev and works through
      // Cloudflare Tunnel in remote access mode
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const apiKey = import.meta.env.VITE_CARDIGAN_API_KEY as string | undefined
      const tokenParam = apiKey ? `?token=${encodeURIComponent(apiKey)}` : ''
      const wsUrl = `${protocol}//${window.location.host}/api/ws/jobs${tokenParam}`

      const ws = new WebSocket(wsUrl)

      ws.onopen = () => {
        console.log('[WebSocket] Connected to job updates')
        setIsConnected(true)
        setConnectionError(null)

        // Start heartbeat to keep connection alive
        heartbeatIntervalRef.current = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send('ping')
          }
        }, 30000) // Ping every 30 seconds
      }

      ws.onmessage = (event) => {
        try {
          const message: WebSocketMessage = JSON.parse(event.data)
          setLastMessage(message)

          // Handle different message types
          if (message.type === 'stats_updated' && message.stats && onStatsUpdate) {
            onStatsUpdate(message.stats)
          } else if (message.job && onJobUpdate) {
            onJobUpdate(message.job, message.type)
          }
        } catch (err) {
          console.error('[WebSocket] Failed to parse message:', err)
        }
      }

      ws.onerror = (event) => {
        console.error('[WebSocket] Connection error:', event)
        setConnectionError(new Error('WebSocket connection error'))
      }

      ws.onclose = (event) => {
        console.log('[WebSocket] Disconnected:', event.code, event.reason)
        setIsConnected(false)

        // Clear heartbeat interval
        if (heartbeatIntervalRef.current) {
          clearInterval(heartbeatIntervalRef.current)
          heartbeatIntervalRef.current = null
        }

        // Attempt reconnection if not intentionally closed
        if (autoReconnect && !isIntentionallyClosed.current) {
          console.log(`[WebSocket] Reconnecting in ${reconnectInterval}ms...`)
          reconnectTimeoutRef.current = setTimeout(() => {
            connect()
          }, reconnectInterval)
        }
      }

      wsRef.current = ws
    } catch (err) {
      console.error('[WebSocket] Failed to create connection:', err)
      setConnectionError(err as Error)

      // Retry connection
      if (autoReconnect && !isIntentionallyClosed.current) {
        reconnectTimeoutRef.current = setTimeout(() => {
          connect()
        }, reconnectInterval)
      }
    }
  }, [autoReconnect, reconnectInterval, onJobUpdate, onStatsUpdate])

  const disconnect = useCallback(() => {
    isIntentionallyClosed.current = true

    // Clear reconnect timeout
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current)
      reconnectTimeoutRef.current = null
    }

    // Clear heartbeat interval
    if (heartbeatIntervalRef.current) {
      clearInterval(heartbeatIntervalRef.current)
      heartbeatIntervalRef.current = null
    }

    // Close WebSocket connection
    if (wsRef.current) {
      wsRef.current.close()
      wsRef.current = null
    }

    setIsConnected(false)
  }, [])

  // Connect on mount, disconnect on unmount
  useEffect(() => {
    isIntentionallyClosed.current = false
    connect()

    return () => {
      disconnect()
    }
  }, [connect, disconnect])

  return {
    isConnected,
    lastMessage,
    connectionError,
  }
}
