"use client";

import { useEffect, useState } from "react";

import { TracePanel } from "@/components/trace/TracePanel";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { apiGet } from "@/lib/api/client";
import type { DashboardState } from "@/lib/api/types";
import { useRefreshTick } from "@/lib/refresh";

export default function ProgressPage() {
  const [state, setState] = useState<DashboardState | null>(null);
  const tick = useRefreshTick();
  useEffect(() => {
    let active = true;
    const load = () => apiGet<DashboardState>("/api/state").then((data) => active && setState(data));
    load();
    const timer = setInterval(load, 5000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [tick]);
  return (
    <div className="grid gap-5 xl:grid-cols-[1fr_420px]">
      <Card className="p-5">
        <h1 className="text-sm font-semibold">实时进度</h1>
        <div className="mt-4 space-y-3">
          {(state?.progress.snapshots ?? []).filter((item) => item.challenge_id).map((item) => (
            <div key={`${item.shard}-${item.challenge_id}`} className="border border-line p-4">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="font-mono text-sm">{item.challenge_id}</div>
                  <div className="mt-1 text-xs text-muted">{item.shard} · {item.worker || "unassigned"}</div>
                </div>
                <Badge>{item.stage} · {item.status}</Badge>
              </div>
              <div className="mt-3 h-2 bg-surface">
                <div className="h-full bg-accent" style={{ width: `${item.percent}%` }} />
              </div>
              <div className="mt-2 text-xs text-muted">{item.message || "waiting"}</div>
            </div>
          ))}
        </div>
      </Card>
      <TracePanel />
    </div>
  );
}
