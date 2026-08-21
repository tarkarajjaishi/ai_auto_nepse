import { getToken } from "next-auth/jwt";
import type { NextRequest } from "next/server";

/**
 * The gate nginx asks about before it proxies /api to the Python service.
 *
 * The API used to inherit HTTP Basic Auth, because the browser already held it for this realm.
 * Once the terminal logs in with a cookie instead, that is gone — so without this the API would
 * be either wide open or prompting for a password the user no longer has.
 *
 * nginx `auth_request` replays the original request's headers (Cookie included) here and reads
 * ONLY the status: 2xx allows, 401/403 denies, anything else is a 500. Keep the body empty.
 */
export async function GET(req: NextRequest) {
  const token = await getToken({ req, secret: process.env.NEXTAUTH_SECRET });
  return new Response(null, { status: token ? 204 : 401 });
}
