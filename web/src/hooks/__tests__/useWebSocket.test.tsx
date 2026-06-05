import { StrictMode, useState } from 'react'
import { describe, test, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, act } from '@testing-library/react'
import { useJobsWebSocket } from '../useWebSocket'

/**
 * Mock WebSocket that records every instantiation so tests can assert
 * how many real connections the hook attempts. Sockets start in
 * CONNECTING state; tests drive onopen/onclose/onmessage manually.
 */
class MockWebSocket {
  static instances: MockWebSocket[] = []

  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3

  url: string
  readyState = MockWebSocket.CONNECTING
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onerror: ((event: unknown) => void) | null = null
  onclose: ((event: { code: number; reason: string }) => void) | null = null
  sent: string[] = []

  constructor(url: string) {
    this.url = url
    MockWebSocket.instances.push(this)
  }

  send(data: string) {
    this.sent.push(data)
  }

  close() {
    // Real browsers do NOT fire onclose synchronously — especially for
    // CONNECTING sockets, the close event arrives asynchronously (or the
    // socket simply aborts). Tests drive late events via simulate helpers.
    this.readyState = MockWebSocket.CLOSED
  }

  // Test helpers
  simulateOpen() {
    this.readyState = MockWebSocket.OPEN
    this.onopen?.()
  }

  simulateMessage(payload: unknown) {
    this.onmessage?.({ data: JSON.stringify(payload) })
  }

  simulateUnexpectedClose() {
    this.readyState = MockWebSocket.CLOSED
    this.onclose?.({ code: 1006, reason: 'abnormal' })
  }
}

/**
 * Mirrors how Queue.tsx consumes the hook: inline callback options that
 * get a fresh identity on every render, plus local state that re-renders.
 */
function Harness({ onJob }: { onJob?: (jobId: number) => void }) {
  const [tick, setTick] = useState(0)
  const { isConnected } = useJobsWebSocket({
    onJobUpdate: (job) => {
      onJob?.(job.id)
    },
    onStatsUpdate: () => {},
  })
  return (
    <button onClick={() => setTick(tick + 1)}>
      {isConnected ? 'connected' : 'disconnected'}:{tick}
    </button>
  )
}

describe('useJobsWebSocket', () => {
  beforeEach(() => {
    MockWebSocket.instances = []
    vi.stubGlobal('WebSocket', MockWebSocket)
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  test('does not recreate the socket when the component re-renders with inline callbacks', () => {
    const { getByRole } = render(<Harness />)
    expect(MockWebSocket.instances).toHaveLength(1)

    act(() => MockWebSocket.instances[0].simulateOpen())

    // Re-render several times — inline callbacks get new identities each time
    const button = getByRole('button')
    act(() => button.click())
    act(() => button.click())
    act(() => button.click())

    expect(MockWebSocket.instances).toHaveLength(1)
    expect(MockWebSocket.instances[0].readyState).toBe(MockWebSocket.OPEN)
  })

  test('reconnects after the socket closes unexpectedly', () => {
    render(<Harness />)
    expect(MockWebSocket.instances).toHaveLength(1)

    act(() => MockWebSocket.instances[0].simulateOpen())
    act(() => MockWebSocket.instances[0].simulateUnexpectedClose())

    // Still just the original socket until the reconnect interval elapses
    expect(MockWebSocket.instances).toHaveLength(1)

    act(() => vi.advanceTimersByTime(3000))
    expect(MockWebSocket.instances).toHaveLength(2)
  })

  test('delivers messages to the latest callback after re-render', () => {
    const firstHandler = vi.fn()
    const secondHandler = vi.fn()

    const { rerender } = render(<Harness onJob={firstHandler} />)
    act(() => MockWebSocket.instances[0].simulateOpen())

    rerender(<Harness onJob={secondHandler} />)
    act(() =>
      MockWebSocket.instances[0].simulateMessage({
        type: 'job_updated',
        job: { id: 42, status: 'completed' },
      })
    )

    expect(firstHandler).not.toHaveBeenCalled()
    expect(secondHandler).toHaveBeenCalledWith(42)
  })

  test('ignores late events from a superseded socket (StrictMode double-mount)', () => {
    // StrictMode runs effect → cleanup → effect on mount: ws1 is created,
    // closed by cleanup, then ws2 is created. In real browsers ws1's close
    // event arrives asynchronously — after ws2 is already live.
    const { getByRole } = render(
      <StrictMode>
        <Harness />
      </StrictMode>
    )
    expect(MockWebSocket.instances).toHaveLength(2)
    const [ws1, ws2] = MockWebSocket.instances

    act(() => ws2.simulateOpen())
    expect(getByRole('button').textContent).toContain('connected')

    // ws1's close event finally arrives — it must not corrupt the live
    // connection's state or schedule a reconnect
    act(() => ws1.simulateUnexpectedClose())
    expect(getByRole('button').textContent).toContain('connected')
    expect(getByRole('button').textContent).not.toContain('disconnected')

    act(() => vi.advanceTimersByTime(10000))
    expect(MockWebSocket.instances).toHaveLength(2)
  })
})
