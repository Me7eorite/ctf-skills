import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useEventStream } from './useEventStream'

class FakeEventSource {
  static instances: FakeEventSource[] = []
  onopen: ((event: Event) => void) | null = null
  onmessage: ((event: MessageEvent<string>) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  closed = false
  constructor(public url: string) {
    FakeEventSource.instances.push(this)
  }
  close() {
    this.closed = true
  }
  triggerError() {
    this.onerror?.(new Event('error'))
  }
  triggerOpen() {
    this.onopen?.(new Event('open'))
  }
}

describe('useEventStream', () => {
  beforeEach(() => {
    FakeEventSource.instances = []
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('reconnects with exponential backoff doubling up to 4s', () => {
    vi.useFakeTimers()
    const delays: number[] = []
    const schedule = (fn: () => void, delay: number) => {
      delays.push(delay)
      return setTimeout(fn, delay) as unknown as number
    }
    const stream = useEventStream(() => {}, {
      factory: (url) => new FakeEventSource(url) as unknown as EventSource,
      schedule,
    })

    stream.open('/api/events/stream')
    // First connection alive
    expect(FakeEventSource.instances).toHaveLength(1)

    // Each error should schedule a reconnect with backoff 1s, then 2s, then 4s.
    FakeEventSource.instances[0].triggerError()
    expect(delays.at(-1)).toBe(1000)
    vi.advanceTimersByTime(1000)
    expect(FakeEventSource.instances).toHaveLength(2)

    FakeEventSource.instances[1].triggerError()
    expect(delays.at(-1)).toBe(2000)
    vi.advanceTimersByTime(2000)

    FakeEventSource.instances[2].triggerError()
    expect(delays.at(-1)).toBe(4000)
    vi.advanceTimersByTime(4000)

    FakeEventSource.instances[3].triggerError()
    expect(delays.at(-1)).toBe(4000)
  })

  it('resets backoff to 1s after a successful open', () => {
    vi.useFakeTimers()
    const delays: number[] = []
    const stream = useEventStream(() => {}, {
      factory: (url) => new FakeEventSource(url) as unknown as EventSource,
      schedule: (fn, delay) => {
        delays.push(delay)
        return setTimeout(fn, delay) as unknown as number
      },
    })
    stream.open('/api/events/stream')

    FakeEventSource.instances[0].triggerError()
    vi.advanceTimersByTime(1000)
    FakeEventSource.instances[1].triggerError()
    vi.advanceTimersByTime(2000)
    // Successful open should reset backoff.
    FakeEventSource.instances[2].triggerOpen()
    FakeEventSource.instances[2].triggerError()

    expect(delays.at(-1)).toBe(1000)
  })
})
