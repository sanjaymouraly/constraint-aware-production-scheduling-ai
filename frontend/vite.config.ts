import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'node:path'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    strictPort: false,
    // The sandbox/remote preview host changes on every deployment, so the
    // dev server accepts any host header.  Never expose this server publicly.
    allowedHosts: true,
    proxy: {
      '/api': {
        target: process.env.VITE_API_TARGET || 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  preview: { host: '0.0.0.0', port: 4173, allowedHosts: true },
  build: { outDir: 'dist', sourcemap: false, chunkSizeWarningLimit: 1200 },
})
