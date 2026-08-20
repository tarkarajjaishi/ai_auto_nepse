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
};

export default nextConfig;
