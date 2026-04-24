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
        target: 'http://metadata.neighborhood:8100',
        changeOrigin: true,
        ws: true,
      },
    },
  },
})
