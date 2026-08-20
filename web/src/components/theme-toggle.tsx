"use client";

import { AnimatePresence, motion } from "motion/react";
import { Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useTheme } from "@/store/theme";

/**
 * Sun/moon toggle. The icon shows the theme you would SWITCH TO, which is the convention every
 * terminal uses — a moon while you are in light mode reads as "go dark".
 *
 * Mounted-gate: the server has no localStorage, so it cannot know which icon is correct. Without
 * the gate React renders one icon on the server, a different one on the client, and logs a
 * hydration mismatch. The placeholder keeps the header from reflowing while that resolves.
 */
export function ThemeToggle() {
  const { theme, toggle } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  if (!mounted) {
    return <div className="size-9" aria-hidden />;
  }

  const dark = theme === "dark";
  const Icon = dark ? Sun : Moon;
  const label = dark ? "Switch to light" : "Switch to dark";

  return (
    <Tooltip>
      {/* Base UI uses `render` where Radix used `asChild` */}
      <TooltipTrigger
        render={
          <Button
            variant="ghost"
            size="icon"
            onClick={toggle}
            aria-label={label}
            className="relative size-9 overflow-hidden text-muted-foreground hover:text-foreground"
          />
        }
      >
        <AnimatePresence mode="wait" initial={false}>
          <motion.span
            key={theme}
            initial={{ y: 14, opacity: 0, rotate: -35 }}
            animate={{ y: 0, opacity: 1, rotate: 0 }}
            exit={{ y: -14, opacity: 0, rotate: 35 }}
            transition={{ duration: 0.18, ease: [0.22, 1, 0.36, 1] }}
            className="absolute inset-0 grid place-items-center"
          >
            <Icon className="size-[1.05rem]" strokeWidth={2} />
          </motion.span>
        </AnimatePresence>
      </TooltipTrigger>
      <TooltipContent side="bottom">{label}</TooltipContent>
    </Tooltip>
  );
}
