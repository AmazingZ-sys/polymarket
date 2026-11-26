// Vercel evaluates this file at deployment time, outside the browser bundle.
const apiOrigin = new URL(process.env.PQ_API_ORIGIN || '')
if (
  apiOrigin.protocol !== 'https:' ||
  apiOrigin.username ||
  apiOrigin.password ||
  apiOrigin.pathname !== '/' ||
  apiOrigin.search ||
  apiOrigin.hash
) {
  throw new Error('Set PQ_API_ORIGIN to the HTTPS origin of your Hono server.')
}

export const config = {
  framework: 'vite',
  buildCommand: 'pnpm build',
  outputDirectory: 'dist',
  rewrites: [
    { source: '/api/:path*', destination: apiOrigin.origin + '/api/:path*' },
    { source: '/((?!api/|assets/).*)', destination: '/index.html' },
  ],
}
