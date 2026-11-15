import type { NextConfig } from "next";

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL
  ? new URL(process.env.NEXT_PUBLIC_SUPABASE_URL)
  : null;

const nextConfig: NextConfig = {
  cacheComponents: true,
  images: {
    deviceSizes: [1320, 1920],
    imageSizes: [],
    minimumCacheTTL: 31536000,
    // Local Supabase serves covers from 127.0.0.1, which the optimizer treats as a private address.
    dangerouslyAllowLocalIP: supabaseUrl?.hostname === "127.0.0.1" || supabaseUrl?.hostname === "localhost",
    remotePatterns: supabaseUrl
      ? [
          {
            hostname: supabaseUrl.hostname,
            port: supabaseUrl.port,
            protocol: supabaseUrl.protocol.replace(":", "") as "http" | "https",
          },
        ]
      : [],
  },
  partialPrefetching: true,
  async redirects() {
    return [
      { destination: "/collections/all", permanent: true, source: "/books" },
      { destination: "/products/:slug", permanent: true, source: "/books/:slug" },
    ];
  },
  poweredByHeader: false,
  reactCompiler: true,
  turbopack: {
    rules: {
      "*.css": {
        as: "*.css",
        loaders: ["@tailwindcss/turbopack"],
      },
    },
  },
};

export default nextConfig;
