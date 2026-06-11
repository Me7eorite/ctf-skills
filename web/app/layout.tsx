"use client";

import "./globals.css";

import { Activity, Database, Flag, Layers3, ListChecks, Terminal, Zap } from "lucide-react";
import Link from "next/link";
import type * as React from "react";
import { useEffect, useState } from "react";

import { RefreshButton } from "@/components/header/RefreshButton";
import { ValidateButton } from "@/components/header/ValidateButton";
import { WorkerButton } from "@/components/header/WorkerButton";
import { apiGet } from "@/lib/api/client";
import { RefreshProvider } from "@/lib/refresh";
import { ToastProvider } from "@/lib/toast";

const nav = [
  { href: "/", label: "生产概览", icon: Activity },
  { href: "/progress", label: "实时进度", icon: Zap },
  { href: "/seeds", label: "种子配置", icon: ListChecks },
  { href: "/challenges", label: "题目卡片", icon: Flag },
  { href: "/shards", label: "任务分片", icon: Layers3 },
  { href: "/logs", label: "运行日志", icon: Terminal },
];

function DemoBadge() {
  const [demo, setDemo] = useState<boolean | null>(null);
  useEffect(() => {
    apiGet<{ demo: boolean }>("/api/mode")
      .then((mode) => setDemo(mode.demo))
      .catch(() => setDemo(false));
  }, []);
  return (
    <div className="h-7 w-20">
      {demo ? (
        <div className="flex h-7 items-center justify-center border border-accent/60 bg-accent/10 text-xs font-semibold text-accent">
          DEMO
        </div>
      ) : null}
    </div>
  );
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <RefreshProvider>
          <ToastProvider>
            <div className="flex min-h-screen bg-surface text-ink">
              <aside className="hidden w-64 border-r border-line bg-card p-4 lg:block">
                <div className="flex items-center gap-3 px-2 py-4">
                  <div className="grid size-9 place-items-center border border-accent/60 bg-accent/10 text-accent">
                    <Database className="size-5" />
                  </div>
                  <div>
                    <div className="text-sm font-semibold">Challenge Factory</div>
                    <div className="text-xs text-muted">AI control plane</div>
                  </div>
                </div>
                <nav className="mt-5 grid gap-1">
                  {nav.map((item) => (
                    <Link
                      key={item.href}
                      href={item.href}
                      className="flex h-10 items-center gap-3 border border-transparent px-3 text-sm text-muted transition hover:border-line hover:text-ink"
                    >
                      <item.icon className="size-4" />
                      {item.label}
                    </Link>
                  ))}
                </nav>
              </aside>
              <main className="min-w-0 flex-1">
                <header className="flex h-16 items-center justify-between border-b border-line bg-card px-5">
                  <div>
                    <div className="text-sm font-semibold">AI 网络安全教育演示面板</div>
                    <div className="text-xs text-muted">实时队列、Agent 轨迹、训练题卡片</div>
                  </div>
                  <div className="flex items-center gap-2">
                    <RefreshButton />
                    <WorkerButton />
                    <ValidateButton />
                    <DemoBadge />
                  </div>
                </header>
                <div className="p-5">{children}</div>
              </main>
            </div>
          </ToastProvider>
        </RefreshProvider>
      </body>
    </html>
  );
}
