import type { TraceEvent } from "@/lib/api/types";

export function connectTrace(
  onEvent: (event: TraceEvent) => void,
  onStatus: (connected: boolean) => void,
) {
  const source = new EventSource("/api/trace/stream");
  source.addEventListener("open", () => onStatus(true));
  source.addEventListener("error", () => onStatus(false));
  source.addEventListener("trace", (event) => {
    onStatus(true);
    onEvent(JSON.parse((event as MessageEvent).data) as TraceEvent);
  });
  return () => source.close();
}
