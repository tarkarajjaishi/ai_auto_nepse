import NextAuth from "next-auth";
import { authOptions } from "@/lib/auth";

/* Mounted at /admin/auth/* rather than /api/auth/* — see the note in lib/auth.ts: /api belongs
   to the Python API in both production and development. */
const handler = NextAuth(authOptions);

export { handler as GET, handler as POST };
