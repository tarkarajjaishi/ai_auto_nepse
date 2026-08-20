import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /**
   * The VPS has ~300 MB of RAM free and 2.7 GB of swap already in use — a `pnpm install` plus a
   * production build there would OOM, and the box also serves three other sites and the Streamlit
   * app that this one is replacing. `standalone` moves that cost to the machine doing the build:
   * the output is `server.js` plus only the node_modules actually traced as reachable, so the
   * deploy ships a runnable tree and the box needs nothing but the `node` already at
   * /usr/local/bin/node. Not `export`, because the Account page needs Auth.js route handlers and
   * a static export cannot host them.
   */
  output: "standalone",

  // The build runs from web/, but the repo root is the parent. Say so explicitly, or file tracing
  // walks up looking for a lockfile, finds the Python project, and warns about the inferred root.
  outputFileTracingRoot: __dirname,

  /**
   * Dev only: serve the Python API from /api of this same origin, exactly as nginx does in
   * production.
   *
   * Without it, dev is the only environment where the API is cross-origin — so CORS mistakes stay
   * invisible until deploy, the dev bundle needs an absolute hostname that must never survive into
   * production (it nearly did, via .env.local), and the dev server has to run on the one port the
   * API happens to allow. With it, both environments resolve /api the same way and none of that
   * applies. NEXT_PUBLIC_API_BASE still overrides, for pointing dev at a tunnelled VPS.
   */
  async rewrites() {
    if (process.env.NODE_ENV !== "development") return [];
    const target = process.env.API_ORIGIN ?? "http://127.0.0.1:8600";
    return [{ source: "/api/:path*", destination: `${target}/api/:path*` }];
  },
};

export default nextConfig;
