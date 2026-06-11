"use client";

import { useEffect, useState } from "react";

import { Card } from "@/components/ui/card";
import { apiGet } from "@/lib/api/client";
import type { DashboardState } from "@/lib/api/types";
import { useRefreshTick } from "@/lib/refresh";

export default function LogsPage() {
  const [state, setState] = useState<DashboardState | null>(null);
  const [content, setContent] = useState("");
  const tick = useRefreshTick();
  useEffect(() => {
    apiGet<DashboardState>("/api/state").then(setState);
  }, [tick]);
  async function readLog(name: string) {
    const result = await apiGet<{ content: string }>(`/api/logs/${encodeURIComponent(name)}`);
    setContent(result.content);
  }
  return (
    <div className="grid gap-5 xl:grid-cols-[320px_1fr]">
      <Card className="p-4">
        <h1 className="text-sm font-semibold">运行日志</h1>
        <div className="mt-4 space-y-2">
          {(state?.logs ?? []).map((log) => (
            <button key={log.name} className="block w-full border border-line p-3 text-left text-sm hover:border-accent" onClick={() => readLog(log.name)}>
              <div>{log.name}</div>
              <div className="mt-1 text-xs text-muted">{log.size} bytes · {log.updated}</div>
            </button>
          ))}
        </div>
      </Card>
      <Card className="min-h-[480px] p-4">
        <pre className="whitespace-pre-wrap text-xs leading-5 text-muted">{content || "选择日志查看内容"}</pre>
      </Card>
    </div>
  );
}
