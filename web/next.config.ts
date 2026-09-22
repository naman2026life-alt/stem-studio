import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Keep the login-started local UI independent from subsequent Vercel builds.
  distDir: process.env.STEM_STUDIO_LOCAL_WEB === "1" ? ".next-mac" : ".next",
  allowedDevOrigins: ["127.0.0.1"],
  poweredByHeader: false,
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Permissions-Policy", value: "camera=(), geolocation=(), microphone=(self)" },
        ],
      },
    ];
  },
};

export default nextConfig;
