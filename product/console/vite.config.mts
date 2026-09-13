import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

/*
 * The console talks to the API over same-origin relative paths (`/health`,
 * `/feed/recent`, …) because in production Caddy serves both from one host
 * (product/deploy/Caddyfile). In dev there is no Caddy, so those paths hit
 * the Vite server itself and 404 — which is why the header sat on DEGRADED
 * with every chip showing a dash.
 *
 * The proxy below is the fix. There is deliberately no mock mode: a stub that
 * answers these routes with plausible-looking numbers is indistinguishable
 * on screen from a working service, and a console that can look healthy with
 * no backend behind it is worse than one that plainly cannot start.
 *
 *   CONCORDE_API_TARGET=http://127.0.0.1:8080 npm run dev
 */

const API_PATHS = ['/health', '/version', '/metrics', '/detect', '/analyze', '/feed', '/history']
const API_TARGET = process.env.CONCORDE_API_TARGET ?? 'http://127.0.0.1:8080'

/** Origin used by the jsdom test environment; see the `test` block below. */
const TEST_ORIGIN = 'http://localhost:5173'

export default defineConfig({
  root: '.',
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      ...Object.fromEntries(API_PATHS.map((p) => [p, { target: API_TARGET, changeOrigin: true }])),
      // The live feed. `ws: true` is what makes the header read FEED WS
      // instead of falling back to 3s polling.
      '/ws': { target: API_TARGET.replace(/^http/, 'ws'), ws: true },
    },
  },
  build: {
    outDir: 'dist',
  },
  test: {
    // jsdom, not the `node` default: every @testing-library render in this
    // package failed with `document is not defined` until this line existed.
    environment: 'jsdom',
    // api.ts fetches relative paths, which is right in the browser (one
    // origin behind Caddy) and fatal under test: both Node's native fetch and
    // msw's interceptor call `new URL(path)` and throw on a bare "/analyze".
    // Pinning the jsdom origin and pointing API_BASE at the same value makes
    // those calls absolute for tests only — the app keeps shipping relative
    // URLs, since VITE_API_BASE is unset in every real build.
    environmentOptions: { jsdom: { url: TEST_ORIGIN } },
    // API_BASE itself is set in .env.test, which Vite loads for `test` mode.
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
})
