import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The dev server runs on http://localhost:5173 = WEB_CLIENT_ORIGIN of the server (CORS).
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true },
})
