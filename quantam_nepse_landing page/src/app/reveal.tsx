"use client";

import { motion, useReducedMotion } from "motion/react";
import type { ReactNode } from "react";

/* Staged entrance for one group of artboard elements.

   The wrapper MUST be `absolute inset-0`. Motion animates `transform`, and a
   transform turns the wrapper into the containing block for every absolutely
   positioned descendant — a plain <div> wrapper would re-anchor the whole group
   to a zero-height box at the top of the stage and scatter the artboard. `.stage`
   has no padding and no border, so `inset-0` reproduces its padding box exactly
   and the children keep the coordinates page.tsx gives them.

   `children` arrives as an RSC payload from the server component, so wrapping
   costs the client bundle nothing beyond this file — page.tsx stays a server
   component, which is the rule the ambient CSS animations depend on. */
export function Reveal({ delay = 0, children }: { delay?: number; children: ReactNode }) {
  const still = useReducedMotion();
  return (
    <motion.div
      className="absolute inset-0"
      initial={{ opacity: 0, ...(still ? null : { y: "8rem" }) }}
      animate={{ opacity: 1, ...(still ? null : { y: 0 }) }}
      transition={{ duration: 0.6, delay, ease: [0.22, 1, 0.36, 1] }}
    >
      {children}
    </motion.div>
  );
}
