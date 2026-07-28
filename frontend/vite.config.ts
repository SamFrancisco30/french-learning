import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Proxy the API and media mounts to the FastAPI backend so the browser only ever
// talks to one origin — no CORS preflight, and <audio> Range requests pass through.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/media': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})
