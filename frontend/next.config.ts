import type { NextConfig } from "next";

const isProd = process.env.NODE_ENV === "production";

// Production: static export served by FastAPI (no rewrites — they are incompatible with export).
// Development: proxy /api/* to the FastAPI dev server so the browser sees a single origin.
const nextConfig: NextConfig = isProd
  ? {
      output: "export",
      images: { unoptimized: true },
    }
  : {
      images: { unoptimized: true },
      // gzip would buffer the proxied SSE stream (/api/stream/prices) in dev.
      compress: false,
      devIndicators: false,
      // Separate build dir so `next build` (e.g. in another shell) doesn't clobber a running dev server.
      distDir: ".next-dev",
      async rewrites() {
        return [
          {
            source: "/api/:path*",
            destination: `${process.env.BACKEND_URL ?? "http://localhost:8000"}/api/:path*`,
          },
        ];
      },
    };

export default nextConfig;
