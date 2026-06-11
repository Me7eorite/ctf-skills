"use client";

import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { apiGet, apiSend } from "@/lib/api/client";
import type { DashboardState } from "@/lib/api/types";
import { useRefreshTick } from "@/lib/refresh";

export default function ShardsPage() {
  const [state, setState] = useState<DashboardState | null>(null);
  const [message, setMessage] = useState("");
  const tick = useRefreshTick();
  const load = () => apiGet<DashboardState>("/api/state").then(setState);
  useEffect(() => {
    load();
  }, [tick]);
  async function requeue(shardState: string, name: string) {
    try {
      const result = await apiSend<{ message: string }>(`/api/shards/${shardState}/${encodeURIComponent(name)}/requeue`, { method: "POST" });
      setMessage(result.message);
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "request failed");
    }
  }
  return (
    <div className="space-y-4">
      {message ? <div className="border border-line bg-card p-3 text-sm text-accent">{message}</div> : null}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {["pending", "running", "failed", "done"].map((shardState) => (
          <Card key={shardState} className="p-4">
            <h2 className="text-sm font-semibold">{shardState}</h2>
            <div className="mt-4 space-y-3">
              {(state?.shards ?? []).filter((item) => item.state === shardState).map((shard) => (
                <div key={shard.name} className="border border-line p-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate font-mono text-xs">{shard.name}</span>
                    <Badge>{shard.count}</Badge>
                  </div>
                  <div className="mt-2 text-xs text-muted">{shard.categories.join(", ")} · {shard.updated}</div>
                  {(shardState === "failed" || shardState === "running") ? (
                    <Button className="mt-3 h-8 text-xs" onClick={() => requeue(shardState, shard.name)}>重新入队</Button>
                  ) : null}
                </div>
              ))}
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
