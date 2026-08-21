import type { NextConfig } from "next";

/* Deployed behind the same nginx vhost as the admin terminal, which is ALSO a
   Next app and already owns /_next. Two Next apps cannot share that path, so
   this one serves its bundle from /_lp/_next via assetPrefix and nginx maps
   /_lp -> this service. Without it the landing page loads as unstyled HTML that
   never hydrates, while every health check stays green.

   standalone because the VPS cannot build: it has ~800MB free and running
   `next build` there OOMs something that is currently serving. */
export default {
  output: "standalone",
  assetPrefix: "/_lp",

  /* DEV ONLY: put the Python API on /api of this same origin, exactly as nginx
     does in production. The access-request form posts to a relative /api path,
     so without this the only environment where that path is cross-origin is the
     one we develop in — and it would fail as a CORS error on a page that renders
     perfectly. The API's own _ALLOW header defaults to port 3000, which is not
     this app's port either, so the flag is not the fix; the rewrite is.

     Note assetPrefix does NOT apply here. It prefixes static assets, not routes
     or rewrites, so /api stays /api on both surfaces. */
  async rewrites() {
    if (process.env.NODE_ENV !== "development") return [];
    const target = process.env.API_ORIGIN ?? "http://127.0.0.1:8600";
    return [{ source: "/api/:path*", destination: `${target}/api/:path*` }];
  },
} satisfies NextConfig;
