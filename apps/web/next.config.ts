import type { NextConfig } from "next";

const supabaseHost = process.env.NEXT_PUBLIC_SUPABASE_URL
  ? new URL(process.env.NEXT_PUBLIC_SUPABASE_URL)
  : null;

const nextConfig: NextConfig = {
  poweredByHeader: false,
  reactCompiler: true,
  images: {
    remotePatterns: supabaseHost
      ? [{ hostname: supabaseHost.hostname, protocol: supabaseHost.protocol.replace(":", "") as "http" | "https" }]
      : [],
  },
  turbopack: {
    rules: { "*.css": { as: "*.css", loaders: ["@tailwindcss/turbopack"] } },
  },
};

export default nextConfig;
