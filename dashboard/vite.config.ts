import { defineConfig } from 'vite';

// Base URL for the SPA. In dev (no env) it's '/' which Vite serves at :5173.
// In production builds we publish under mindframe.softwaresoftware.dev/demo/
// so all asset paths must be prefixed accordingly.
const BASE = process.env.VITE_BASE || '/';

export default defineConfig({
  root: '.',
  publicDir: 'public',
  base: BASE,
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:5174',
        changeOrigin: true,
      },
    },
    // Vite's file watcher reloads the page on any tracked write. The backend
    // writes artifacts and server logs inside this directory, which would
    // kill the in-flight EventSource every time. Exclude them.
    watch: {
      ignored: [
        '**/artifacts/**',
        '**/shares/**',
        '**/server.log',
        '**/*.log',
      ],
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
});
