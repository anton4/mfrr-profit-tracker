import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // Relative asset URLs, so the built UI works under Home Assistant Ingress (/api/hassio_ingress/<token>/)
  base: './',
  server: {
    // Local dev: forward API calls to the backend (uvicorn on :8000)
    proxy: { '/api': 'http://localhost:8000' },
  },
})
