/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Standalone output produces a minimal self-contained server for the
  // production Docker image (no node_modules copy needed).
  output: "standalone",
  poweredByHeader: false,
};

export default nextConfig;
