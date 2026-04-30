/** @type {import('next').NextConfig} */
const isProd = process.env.NODE_ENV === 'production';

const backendOrigin =
  process.env.NEXT_PUBLIC_BACKEND_ORIGIN || 'https://meet.quexio.com';
const wsOrigin = backendOrigin.replace(/^http/, 'ws');

// CSP — keep loose enough for Next.js + posthog + google sign-in to function
// in production. Tighten further as you audit third-party scripts.
const csp = [
  "default-src 'self'",
  // Next.js needs inline + eval for runtime; relax in prod gradually.
  "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://accounts.google.com https://*.posthog.com https://app.posthog.com",
  "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
  "font-src 'self' https://fonts.gstatic.com data:",
  "img-src 'self' data: blob: https://*.googleusercontent.com https://lh3.googleusercontent.com",
  "media-src 'self' blob:",
  `connect-src 'self' ${backendOrigin} ${wsOrigin} https://accounts.google.com https://*.posthog.com https://app.posthog.com https://api.razorpay.com https://lumberjack.razorpay.com`,
  "frame-src https://accounts.google.com https://api.razorpay.com",
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self' https://accounts.google.com",
  "object-src 'none'",
  isProd ? 'upgrade-insecure-requests' : '',
].filter(Boolean).join('; ');

const securityHeaders = [
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  { key: 'X-Frame-Options', value: 'DENY' },
  { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
  {
    key: 'Permissions-Policy',
    value: 'camera=(), microphone=(self), geolocation=(), payment=(self), usb=()',
  },
  { key: 'Content-Security-Policy', value: csp },
];

if (isProd) {
  securityHeaders.push({
    key: 'Strict-Transport-Security',
    value: 'max-age=63072000; includeSubDomains; preload',
  });
}

const nextConfig = {
  output: "standalone",
  reactStrictMode: false, // Disabled for BlockNote compatibility
  images: {
    unoptimized: true,
  },

  // In production builds, strip console.log/info/debug noise but keep
  // console.error and console.warn so real problems still surface.
  compiler: isProd
    ? { removeConsole: { exclude: ['error', 'warn'] } }
    : undefined,

  async headers() {
    return [
      {
        source: '/:path*',
        headers: securityHeaders,
      },
    ];
  },

  webpack: (config, { isServer }) => {
    if (!isServer) {
      config.resolve.fallback = {
        ...config.resolve.fallback,
        fs: false,
        path: false,
        os: false,
      };
    }
    return config;
  },
}

module.exports = nextConfig
