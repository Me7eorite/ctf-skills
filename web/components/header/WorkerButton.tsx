"use client";

import { Play } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { apiPost } from "@/lib/api/client";
import { useRefreshBump } from "@/lib/refresh";
import { useToast } from "@/lib/toast";

export function WorkerButton() {
  const { showToast } = useToast();
  const bump = useRefreshBump();
  const [busy, setBusy] = useState(false);

  const handleClick = async () => {
    if (busy) return;
    setBusy(true);
    const result = await apiPost("/api/actions/worker");
    if (result.ok) {
      showToast(result.message ?? "已启动 Worker");
      bump();
    } else {
      showToast(result.message ?? "请求失败");
    }
    setBusy(false);
  };

  return (
    <Button onClick={handleClick} disabled={busy} title="启动 Worker">
      <Play className="size-4" />
      <span className="hidden sm:inline">启动 Worker</span>
    </Button>
  );
}
