import { withAuth } from "next-auth/middleware";

/**
 * Everything under /admin needs a session, with three holes punched in it:
 *
 *   login      — the page you are sent to; guarding it is a redirect loop
 *   auth/*     — the NextAuth handler itself, including the POST that signs you in
 *   auth-check — the nginx auth_request gate, which must be free to answer 401
 *
 * The matcher is a NEGATIVE lookahead rather than a list of the eighteen guarded routes: a new
 * admin page must be protected by default, and a list is a thing you forget to add to.
 *
 * signIn is repeated here because middleware runs on the edge and never loads authOptions —
 * without it the redirect goes to next-auth's built-in /api/auth/signin, which nginx sends to
 * the Python API.
 */
export default withAuth({ pages: { signIn: "/admin/login" } });

export const config = {
  matcher: ["/admin((?!/login|/auth|/auth-check).*)"],
};
