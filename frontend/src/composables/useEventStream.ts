import { onBeforeUnmount, ref } from 'vue'

const INITIAL_BACKOFF_MS = 1000
const MAX_BACKOFF_MS = 4000

export interface UseEventStreamOptions {
  /**
   * Custom EventSource factory. Tests inject a fake; production callers
   * usually leave this unset so the native global is used.
   */
  factory?: (url: string) => EventSource
  /**
   * Override scheduling for tests; defaults to ``setTimeout``.
   */
  schedule?: (fn: () => void, delay: number) => number
}

export interface EventStreamApi {
  /** Highest event id observed across the lifetime of this stream. */
  lastEventId: ReturnType<typeof ref<number>>
  /** Current reconnect backoff in milliseconds — exposed for diagnostics. */
  nextBackoffMs: ReturnType<typeof ref<number>>
  /** Open or reopen the stream. Idempotent. */
  open: (url: string) => void
  /** Close the connection and stop reconnecting. */
  close: () => void
}

export function useEventStream(
  onMessage: (event: MessageEvent<string>) => void,
  options: UseEventStreamOptions = {},
): EventStreamApi {
  const factory =
    options.factory ?? ((url: string) => new EventSource(url, { withCredentials: false }))
  const schedule = options.schedule ?? ((fn, delay) => window.setTimeout(fn, delay))

  const lastEventId = ref<number>(0)
  const nextBackoffMs = ref<number>(INITIAL_BACKOFF_MS)

  let current: EventSource | null = null
  let lastUrl = ''
  let closed = false
  let pendingReconnect: number | null = null

  function attach(url: string) {
    if (closed) return
    if (pendingReconnect !== null) {
      pendingReconnect = null
    }
    const source = factory(url)
    current = source
    source.onopen = () => {
      nextBackoffMs.value = INITIAL_BACKOFF_MS
    }
    source.onmessage = (event) => {
      const id = Number((event as MessageEvent & { lastEventId?: string }).lastEventId)
      if (!Number.isNaN(id) && id > (lastEventId.value ?? 0)) {
        lastEventId.value = id
      }
      onMessage(event as MessageEvent<string>)
    }
    source.onerror = () => {
      source.close()
      if (closed) return
      const delay = Math.min(MAX_BACKOFF_MS, nextBackoffMs.value ?? INITIAL_BACKOFF_MS)
      nextBackoffMs.value = Math.min(MAX_BACKOFF_MS, delay * 2)
      pendingReconnect = schedule(() => attach(lastUrl), delay)
    }
  }

  function open(url: string) {
    closed = false
    lastUrl = url
    attach(url)
  }

  function close() {
    closed = true
    if (current) {
      current.close()
      current = null
    }
    pendingReconnect = null
  }

  onBeforeUnmount(close)

  return { lastEventId, nextBackoffMs, open, close }
}
