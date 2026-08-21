import { scryptSync, timingSafeEqual } from "node:crypto";
import type { NextAuthOptions } from "next-auth";
import CredentialsProvider from "next-auth/providers/credentials";

/**
 * One account, one password, no user table — this terminal has exactly one operator and a
 * `.txt`-only project has nowhere to put a users table anyway.
 *
 * The password is NOT in this file and never in git. `ADMIN_PASSWORD_HASH` holds
 * `scrypt:<salt>:<hash>`; regenerate it with:
 *
 *   node -e 'const c=require("crypto"),s=c.randomBytes(16).toString("hex");
 *            console.log("scrypt:"+s+":"+c.scryptSync(process.argv[1],s,64).toString("hex"))' "NEW PASSWORD"
 *
 * COLONS, not dollars. The obvious `scrypt$salt$hash` shape is silently destroyed on the way
 * in: Next runs .env through dotenv-expand, which reads `$salt` as a variable reference and
 * substitutes an empty string, so the whole value arrives as the literal "scrypt". The login
 * then rejects the correct password with the ordinary wrong-password message and nothing
 * anywhere says why. systemd EnvironmentFile has the same hazard.
 *
 * scrypt is `node:crypto` — no bcrypt dependency for a single comparison.
 */
const TEN_YEARS = 10 * 365 * 24 * 60 * 60;

function passwordMatches(plain: string, stored: string): boolean {
  const [scheme, salt, expected] = stored.split(":");
  if (scheme !== "scrypt" || !salt || !expected) return false;
  const got = scryptSync(plain, salt, expected.length / 2);
  const want = Buffer.from(expected, "hex");
  /* timingSafeEqual throws on a length mismatch, which would itself leak length */
  return got.length === want.length && timingSafeEqual(got, want);
}

export const authOptions: NextAuthOptions = {
  /**
   * NOT the default /api/auth. In production nginx sends /api to the PYTHON API on 8600, so a
   * handler mounted there would never be reached; in development next.config rewrites /api to
   * the same place. /admin/auth is inside the one path that already proxies to this app.
   * NEXTAUTH_URL must carry the same suffix or the client posts to the wrong origin.
   */
  providers: [
    CredentialsProvider({
      name: "credentials",
      credentials: {
        email: { label: "Email", type: "email" },
        password: { label: "Password", type: "password" },
      },
      authorize(credentials) {
        const email = process.env.ADMIN_EMAIL;
        const hash = process.env.ADMIN_PASSWORD_HASH;
        /* A missing env var must fail CLOSED. Falling through to "no user" would be a login
           page that accepts nothing; falling through to success would be no login page at all. */
        if (!email || !hash) throw new Error("auth is not configured on this server");
        if (!credentials?.email || !credentials.password) return null;
        if (credentials.email.trim().toLowerCase() !== email.toLowerCase()) return null;
        if (!passwordMatches(credentials.password, hash)) return null;
        return { id: "admin", email, name: "Tarka Raj Jaishi" };
      },
    }),
  ],

  /**
   * "Never log me out, on any deploy, until I sign out."
   *
   * Three separate things have to hold for that, and missing any one of them logs the user out
   * silently:
   *
   *   1. maxAge — ten years, on both the session and the JWT. The default is 30 days.
   *   2. A cookie with an Expires date, which next-auth writes from session.maxAge. Without a
   *      maxAge the cookie is a SESSION cookie and dies when the browser closes.
   *   3. A STABLE NEXTAUTH_SECRET. The token is signed with it, so a secret that is generated
   *      at boot (or lives inside the deployed bundle) invalidates every session on restart —
   *      which is exactly the "logged out by the deploy" failure. It lives in
   *      /home/ubuntu/chukul-web.env on the box, which the deploy never touches.
   *
   * updateAge is the sliding-refresh interval, not an expiry: it only rewrites the cookie —
   * and that rewrite is what makes ten years real. Every current browser CLAMPS a cookie to
   * 400 days (RFC 6265bis), so the `Expires: 2036` this sends is stored as ~400 days; measured
   * on the live site, curl kept 399. Because a session check rewrites the cookie at most once a
   * day, the 400-day window slides forward on every visit, so the session ends only after 400
   * days of no visits at all. Raising maxAge further changes nothing; nothing can beat the cap.
   */
  session: { strategy: "jwt", maxAge: TEN_YEARS, updateAge: 24 * 60 * 60 },
  jwt: { maxAge: TEN_YEARS },

  pages: { signIn: "/admin/login", error: "/admin/login" },
};
