"use client";

import { useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { signIn } from "next-auth/react";
import { Check, Eye, EyeOff, Loader2, Lock, Mail } from "lucide-react";

export function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");

    /* redirect:false so a wrong password re-renders this form with a message, instead of
       bouncing to next-auth's own error page and losing what was typed. */
    const res = await signIn("credentials", { redirect: false, email, password });

    if (res?.ok) {
      /* callbackUrl is where middleware wanted to send us before it asked for a login. It is
         validated against /admin: an open redirect here would let a crafted link bounce a
         freshly authenticated user to somebody else's site. */
      const wanted = params.get("callbackUrl") ?? "";
      let dest = "/admin";
      try {
        const path = new URL(wanted, window.location.origin).pathname;
        if (path.startsWith("/admin") && !path.startsWith("/admin/login")) dest = path;
      } catch {
        /* unparseable callbackUrl — fall through to /admin */
      }
      router.replace(dest);
      router.refresh(); /* the layout reads the session on the server; it must re-render */
      return;
    }

    setBusy(false);
    setError(
      res?.error === "CredentialsSignin" || !res?.error
        ? "That email and password do not match."
        : res.error,
    );
  }

  return (
    <form
      onSubmit={submit}
      className="w-full rounded-2xl border border-border bg-card/80 p-7 shadow-2xl backdrop-blur-xl sm:p-8"
    >
      <div className="mb-1 flex items-center gap-2.5 md:hidden">
        <span className="grid size-7 place-items-center rounded-md bg-primary font-mono text-[13px] font-bold text-primary-foreground">
          N
        </span>
        <span className="text-[15px] font-semibold tracking-tight">Nepse_ai</span>
      </div>

      <h2 className="font-heading text-[22px] font-semibold tracking-tight">Sign in</h2>
      <p className="mt-1 text-[13px] text-muted-foreground">
        This terminal is private. One account, one operator.
      </p>

      <label className="mt-7 block text-[12px] font-medium text-muted-foreground" htmlFor="email">
        Email
      </label>
      <div className="group relative mt-1.5">
        <Mail
          className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
          strokeWidth={1.75}
        />
        <input
          id="email"
          name="email"
          type="email"
          required
          autoComplete="username"
          autoFocus
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@example.com"
          className="h-11 w-full rounded-lg border border-border bg-background pl-10 pr-3 text-[14px] outline-none transition placeholder:text-muted-foreground/60 focus:border-primary focus:ring-2 focus:ring-primary/25"
        />
      </div>

      <label
        className="mt-5 block text-[12px] font-medium text-muted-foreground"
        htmlFor="password"
      >
        Password
      </label>
      <div className="relative mt-1.5">
        <Lock
          className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
          strokeWidth={1.75}
        />
        <input
          id="password"
          name="password"
          type={show ? "text" : "password"}
          required
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="••••••••••"
          className="h-11 w-full rounded-lg border border-border bg-background pl-10 pr-11 text-[14px] outline-none transition placeholder:text-muted-foreground/60 focus:border-primary focus:ring-2 focus:ring-primary/25"
        />
        <button
          type="button"
          onClick={() => setShow((s) => !s)}
          aria-label={show ? "Hide password" : "Show password"}
          className="absolute right-1.5 top-1/2 grid size-8 -translate-y-1/2 place-items-center rounded-md text-muted-foreground transition hover:bg-accent hover:text-foreground"
        >
          {show ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
        </button>
      </div>

      {/* Checked and NOT toggleable, because that is the truth: the session is a ten-year
          cookie signed with a secret that lives outside the deployed bundle, so nothing but
          Sign out ends it. A switch that pretended otherwise would be decoration. */}
      <div className="mt-5 flex items-start gap-2.5">
        <span className="mt-px grid size-4 shrink-0 place-items-center rounded border border-primary bg-primary">
          <Check className="size-3 text-primary-foreground" strokeWidth={3} />
        </span>
        <span className="text-[12px] leading-snug text-muted-foreground">
          <span className="font-medium text-foreground">Remember me — always on.</span> You stay
          signed in on this device through restarts and deploys, until you sign out.
        </span>
      </div>

      {error ? (
        <p
          role="alert"
          className="mt-5 rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2.5 text-[13px] text-destructive"
        >
          {error}
        </p>
      ) : null}

      <button
        type="submit"
        disabled={busy}
        className="mt-6 flex h-11 w-full items-center justify-center gap-2 rounded-lg bg-primary text-[14px] font-semibold text-primary-foreground transition hover:opacity-90 disabled:opacity-60"
      >
        {busy ? <Loader2 className="size-4 animate-spin" /> : null}
        {busy ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}
