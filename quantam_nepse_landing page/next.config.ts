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
} satisfies NextConfig;
