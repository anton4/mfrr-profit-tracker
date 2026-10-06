import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Local dev: forward API calls to the backend (uvicorn on :8000)
    proxy: { '/api': 'http://localhost:8000' },
  },
})
