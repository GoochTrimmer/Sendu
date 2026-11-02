import type { NextConfig } from "next";

// Cache Components is off: every page is per-user and reads the session, so
// classic dynamic rendering is simpler and equally fast here.
const nextConfig: NextConfig = {
  images: { unoptimized: true }, // listing images are hot-linked from marketplaces; never stored
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
