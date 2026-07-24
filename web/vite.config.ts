import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 3000,
    host: true,  // Listen on all interfaces (required for metadata.neighborhood alias)
    allowedHosts: ['metadata.neighborhood', 'localhost', 'cardigan.bymarkriechers.com'],
    proxy: {
      '/api': {
        // Same-machine dev API. Defaults to localhost (works without the
        // metadata.neighborhood /etc/hosts alias); override via env if needed.
        target: process.env.VITE_API_PROXY_TARGET || 'http://localhost:8100',
        changeOrigin: true,
        ws: true,
      },
    },
  },
})
