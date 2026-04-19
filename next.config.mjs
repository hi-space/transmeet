import withPWAInit from '@ducanh2912/next-pwa'

const withPWA = withPWAInit({
  dest: 'public',
  disable: process.env.NODE_ENV === 'development',
  cacheOnFrontEndNav: true,
  aggressiveFrontEndNavCaching: true,
  reloadOnOnline: true,
})

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: 'export', // static HTML/JS/CSS for S3+CloudFront hosting
  trailingSlash: true, // /page → /page/index.html for S3 path resolution
  transpilePackages: ['react-markdown', 'remark-gfm'],
}

export default withPWA(nextConfig)
