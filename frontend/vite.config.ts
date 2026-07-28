// `vitest/config` re-exports vite's defineConfig with the extra `test` key typed;
// vite's own defineConfig would reject it under tsconfig.node.json's strict check.
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// Dev server proxies /api to the FastAPI backend so the browser sees a single origin.
// This deliberately mirrors the production deployment (§13: one container behind the
// department reverse proxy), which keeps session cookies same-origin and means we never
// need a credentialed CORS configuration.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: false,
      },
    },
  },
  test: {
    environment: 'jsdom',
    // No global describe/it/expect: every test file imports them from "vitest" so that
    // tsconfig.app.json (which includes src/**, tests included) typechecks them without
    // needing an ambient "vitest/globals" types entry in the app's compiler options.
    globals: false,
    restoreMocks: true,
  },
})
