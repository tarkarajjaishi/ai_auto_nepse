"use client";

import { SessionProvider } from "next-auth/react";

/**
 * basePath is NOT optional here.
 *
 * next-auth's browser client defaults to /api/auth, and reads NEXTAUTH_URL only on the server —
 * it is not a NEXT_PUBLIC_ variable, so the bundle cannot see it. Left at the default, signIn()
 * and useSession() would POST to /api/auth/*, which nginx hands to the Python API: a 404 that
 * looks exactly like a wrong password.
 */
export function AuthProvider({ children }: { children: React.ReactNode }) {
  return <SessionProvider basePath="/admin/auth">{children}</SessionProvider>;
}
