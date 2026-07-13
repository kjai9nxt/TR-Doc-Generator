import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The React dev server proxies /api to the FastAPI backend (server.py on :8000),
// so `npm run dev` and the Python API run side by side with no CORS friction.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})
