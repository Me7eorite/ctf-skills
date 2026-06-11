"use client";

import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { apiGet } from "@/lib/api/client";
import type { DashboardState } from "@/lib/api/types";
import { useRefreshTick } from "@/lib/refresh";

export default function SeedsPage() {
  const [state, setState] = useState<DashboardState | null>(null);
  const tick = useRefreshTick();
  useEffect(() => {
    apiGet<DashboardState>("/api/state").then(setState);
  }, [tick]);
  return (
    <Card className="p-5">
      <h1 className="text-sm font-semibold">种子配置</h1>
      <div className="mt-4 grid gap-3 md:grid-cols-2">
        {(state?.seeds ?? []).map((seed) => (
          <div key={seed.id} className="border border-line p-4">
            <div className="flex items-center gap-2">
              <span className="font-mono text-sm">{seed.id}</span>
              <Badge>{seed.category}</Badge>
            </div>
            <div className="mt-3 text-sm font-medium">{seed.title}</div>
            <p className="mt-1 text-xs leading-5 text-muted">{seed.learning_objective}</p>
          </div>
        ))}
      </div>
    </Card>
  );
}
