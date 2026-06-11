import type * as React from "react";

import { cn } from "@/lib/utils";

export function Badge({ className, ...props }: React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn(
        "inline-flex items-center border border-line bg-card px-2 py-1 text-xs font-medium text-ink",
        className,
      )}
      {...props}
    />
  );
}
