"use client";

import { motion } from "framer-motion";
import { Layers3, PackageCheck, ShieldCheck, Trophy } from "lucide-react";
import { useEffect, useState } from "react";

import { Card } from "@/components/ui/card";
import { apiGet } from "@/lib/api/client";
import type { DashboardState } from "@/lib/api/types";
import { useRefreshTick } from "@/lib/refresh";

const metricIcons = [Trophy, ShieldCheck, PackageCheck, Layers3];

export default function OverviewPage() {
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

  const summary = state?.summary;
  const values = [
    ["题目总数", summary?.challenges ?? 0, `Web ${summary?.categories.web ?? 0} / Pwn ${summary?.categories.pwn ?? 0} / Re ${summary?.categories.re ?? 0}`],
    ["EXP 已通过", summary?.validated ?? 0, "reference solve status"],
    ["构建已通过", summary?.built ?? 0, "build_status passed"],
    ["活动队列", (summary?.queue.pending ?? 0) + (summary?.queue.running ?? 0), `${summary?.queue.failed ?? 0} failed / ${summary?.queue.done ?? 0} done`],
  ];

  return (
    <div className="space-y-5">
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {values.map(([label, value, note], index) => {
          const Icon = metricIcons[index];
          return (
            <motion.div key={label} initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: index * 0.05 }}>
              <Card className="p-5">
                <div className="flex items-center justify-between text-xs text-muted">
                  <span>{label}</span>
                  <Icon className="size-4 text-accent" />
                </div>
                <div className="mt-5 text-3xl font-semibold">{value}</div>
                <div className="mt-2 text-xs text-muted">{note}</div>
              </Card>
            </motion.div>
          );
        })}
      </div>
      <Card className="p-5">
        <h2 className="text-sm font-semibold">最近题目</h2>
        <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {(state?.challenges ?? []).slice(-6).reverse().map((challenge) => (
            <div key={challenge.id} className="border border-line p-4">
              <div className="text-sm font-medium">{challenge.title}</div>
              <div className="mt-1 text-xs text-muted">{challenge.id} · {challenge.runtime} / {challenge.framework}</div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
