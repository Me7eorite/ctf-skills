"use client";

import { ShieldCheck } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { apiPost } from "@/lib/api/client";
import { useRefreshBump } from "@/lib/refresh";
import { useToast } from "@/lib/toast";

export function ValidateButton() {
  const { showToast } = useToast();
  const bump = useRefreshBump();
  const [busy, setBusy] = useState(false);

  const handleClick = async () => {
    if (busy) return;
    setBusy(true);
    const result = await apiPost("/api/actions/validate");
    if (result.ok) {
      showToast(result.message ?? "已触发重新验证");
      bump();
    } else {
      showToast(result.message ?? "请求失败");
    }
    setBusy(false);
  };

  return (
    <Button onClick={handleClick} disabled={busy} title="重新验证">
      <ShieldCheck className="size-4" />
      <span className="hidden sm:inline">重新验证</span>
    </Button>
  );
}
