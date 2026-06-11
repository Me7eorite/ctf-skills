"use client";

import { RefreshCw } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { useRefreshBump } from "@/lib/refresh";

export function RefreshButton() {
  const bump = useRefreshBump();
  const [spinning, setSpinning] = useState(false);

  const handleClick = () => {
    bump();
    setSpinning(true);
    setTimeout(() => setSpinning(false), 250);
  };

  return (
    <Button onClick={handleClick} aria-label="刷新" title="刷新">
      <RefreshCw className={"size-4" + (spinning ? " animate-spin" : "")} />
      <span className="hidden sm:inline">刷新</span>
    </Button>
  );
}
