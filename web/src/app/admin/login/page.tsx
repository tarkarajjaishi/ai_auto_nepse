import { Suspense } from "react";
import type { Metadata } from "next";
import { LoginForm } from "./form";

export const metadata: Metadata = {
  title: "Sign in — Nepse_ai",
  robots: { index: false, follow: false },
};

/**
 * The terminal's front door. Two columns on a wide screen, one on a narrow one: the pitch on
 * the left so the page is not a lone box on a black field, the card on the right.
 *
 * No chrome around it — the admin layout renders its header and rail only when there is a
 * session, so this page arrives bare.
 */
export default function LoginPage() {
  return (
    <div className="relative grid min-h-dvh place-items-center overflow-hidden bg-background px-6 py-10">
      {/* Two soft brass washes. Pure CSS, no image: the reference's blobs at 4% opacity, which
          is as far as this palette goes before the card stops reading as the brightest thing. */}
      <div
        aria-hidden
        className="pointer-events-none absolute -left-40 -top-40 size-[36rem] rounded-full opacity-[0.07] blur-3xl"
        style={{ background: "radial-gradient(circle, var(--color-primary), transparent 70%)" }}
      />
      <div
        aria-hidden
        className="pointer-events-none absolute -bottom-52 -right-32 size-[32rem] rounded-full opacity-[0.05] blur-3xl"
        style={{ background: "radial-gradient(circle, var(--color-up), transparent 70%)" }}
      />

      <div className="relative grid w-full max-w-5xl items-center gap-12 md:grid-cols-2">
        <div className="hidden md:block">
          <div className="mb-6 flex items-center gap-2.5">
            <span className="grid size-8 place-items-center rounded-lg bg-primary font-mono text-[15px] font-bold text-primary-foreground">
              N
            </span>
            <span className="text-[17px] font-semibold tracking-tight">Nepse_ai</span>
          </div>

          <h1 className="font-heading text-[42px] font-bold leading-[1.08] tracking-tight">
            The NEPSE terminal,
            <br />
            built on your own data.
          </h1>

          <p className="mt-5 max-w-md text-[14px] leading-relaxed text-muted-foreground">
            Eighteen screens over the full floorsheet archive — broker flow, operator radar,
            swing boards and the live NAASA feed. Private, and single-seat by design.
          </p>

          <div className="mt-8 flex flex-wrap gap-x-8 gap-y-3 text-[12px] text-muted-foreground">
            <span>
              <span className="font-mono text-[15px] font-semibold text-foreground">593</span>{" "}
              symbols
            </span>
            <span>
              <span className="font-mono text-[15px] font-semibold text-foreground">13</span>{" "}
              years of bars
            </span>
            <span>
              <span className="font-mono text-[15px] font-semibold text-foreground">1s</span>{" "}
              live quotes
            </span>
          </div>
        </div>

        <Suspense fallback={null}>
          <LoginForm />
        </Suspense>
      </div>
    </div>
  );
}
