"use client";

import { useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { connectTrace } from "@/lib/api/trace";
import type { TraceEvent } from "@/lib/api/types";

export function TracePanel() {
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    return connectTrace(
      (event) => setEvents((current) => [...current.slice(-80), event]),
      setConnected,
    );
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [events]);

  return (
    <section className="border border-line bg-card p-4">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-sm font-semibold">Agent Trace</h2>
        <Badge className={connected ? "border-accent/60 bg-accent/10 text-accent" : "border-amber-500 bg-amber-50 text-amber-700"}>
          {connected ? "streaming" : "reconnecting..."}
        </Badge>
      </div>
      <div className="max-h-[440px] space-y-3 overflow-y-auto pr-2">
        {events.length ? (
          events.map((event, index) => (
            <div key={`${event.ts}-${index}`} className="border-l border-line pl-3 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-xs text-accent">{event.worker || "idle"}</span>
                <Badge>{event.stage || "idle"}</Badge>
                <Badge>{event.status || "unknown"}</Badge>
              </div>
              <p className="mt-2 text-xs leading-5 text-muted">{event.message || event.log || "waiting for trace data"}</p>
              {event.file || event.tool ? (
                <div className="mt-1 text-xs text-muted">
                  {event.file ? <span>{event.file}</span> : null}
                  {event.tool ? <span className="ml-2">{event.tool}</span> : null}
                </div>
              ) : null}
            </div>
          ))
        ) : (
          <div className="py-16 text-center text-sm text-muted">等待 Agent 轨迹事件</div>
        )}
        <div ref={endRef} />
      </div>
    </section>
  );
}
