/** @type {import('next').NextConfig} */
const nextConfig = {
  // The marketing site has no backend of its own — it is prerendered from typed content
  // modules and served as static files behind a CDN. Anything that needs live data belongs
  // in an application (ADR-0001), not here.
  output: 'export',
  reactStrictMode: true,
  trailingSlash: false,
  images: { unoptimized: true },
};

export default nextConfig;
