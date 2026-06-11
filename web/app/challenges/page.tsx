"use client";

import { motion } from "framer-motion";
import { Binary, Globe2, Shield } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { apiGet } from "@/lib/api/client";
import type { Challenge, DashboardState } from "@/lib/api/types";
import { useRefreshTick } from "@/lib/refresh";

const icons = { web: Globe2, pwn: Shield, re: Binary };
const difficultyStars: Record<string, number> = { easy: 1, medium: 3, hard: 5 };

function maskedFlag(challenge: Challenge) {
  return `${challenge.category}{****}`;
}

export default function ChallengesPage() {
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
  const stageById = useMemo(() => {
    const map = new Map<string, string>();
    for (const snapshot of state?.progress.snapshots ?? []) {
      if (snapshot.challenge_id) map.set(snapshot.challenge_id, `${snapshot.stage}:${snapshot.status}`);
    }
    return map;
  }, [state]);

  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      {(state?.challenges ?? []).map((challenge) => {
        const Icon = icons[challenge.category as keyof typeof icons] ?? Shield;
        const stars = difficultyStars[challenge.difficulty] ?? 2;
        const stage = stageById.get(challenge.id) ?? `${challenge.build_status}/${challenge.solve_status}`;
        return (
          <Card key={challenge.id} className="p-5">
            <div className="flex items-start justify-between gap-3">
              <div className="grid size-10 place-items-center border border-line text-accent">
                <Icon className="size-5" />
              </div>
              <motion.div key={stage} initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }}>
                <Badge className="border-accent/50 text-accent">{stage}</Badge>
              </motion.div>
            </div>
            <h2 className="mt-5 text-base font-semibold">{challenge.title}</h2>
            <div className="mt-1 font-mono text-xs text-muted">{challenge.id}</div>
            <div className="mt-4 flex items-center justify-between text-xs text-muted">
              <span>{"★".repeat(stars)}{"☆".repeat(Math.max(0, 5 - stars))}</span>
              <span>{maskedFlag(challenge)}</span>
            </div>
            <div className="mt-4 text-xs text-muted">{challenge.runtime} / {challenge.framework}</div>
          </Card>
        );
      })}
    </div>
  );
}
