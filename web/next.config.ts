import type { NextConfig } from "next";

// The browser only talks to Next.js; /api/* is passed to the copilot API, so the session
// cookie stays first-party and no CORS is needed. API_URL is read at build time.
const API_URL = process.env.API_URL ?? "http://127.0.0.1:8020";

const config: NextConfig = {
  devIndicators: false,
  output: process.env.BUILD_STANDALONE ? "standalone" : undefined,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_URL}/:path*` }];
  },
};

export default config;
