"use client";

import { motion } from "motion/react";

import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/**
 * One number, its label, and what it means. The `note` is not decoration — every figure on this
 * terminal is downstream of a stated rule, and a number without its rule is how "0 actionable"
 * gets read as "the scan is broken".
 */
export function StatTile({
  label,
  value,
  note,
  tone = "default",
  loading,
  index = 0,
}: {
  label: string;
  value: React.ReactNode;
  note?: string;
  tone?: "default" | "up" | "down" | "primary";
  loading?: boolean;
  index?: number;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.28, delay: index * 0.04, ease: [0.22, 1, 0.36, 1] }}
      className="rounded-lg border border-border bg-card p-3.5"
    >
      <div className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground">
        {label}
      </div>
      {loading ? (
        <Skeleton className="mt-2 h-7 w-20" />
      ) : (
        <div
          className={cn(
            "mt-1 font-mono text-2xl font-semibold tabular-nums tracking-tight",
            tone === "up" && "text-up",
            tone === "down" && "text-down",
            tone === "primary" && "text-primary",
          )}
        >
          {value}
        </div>
      )}
      {note && <div className="mt-1 text-[11px] leading-snug text-muted-foreground">{note}</div>}
    </motion.div>
  );
}
