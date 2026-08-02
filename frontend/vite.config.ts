import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
// Dev-only self-signed HTTPS: browsers require a secure context for
// getUserMedia (the check-capture camera), and phones reach us by LAN IP.
import basicSsl from '@vitejs/plugin-basic-ssl'

export default defineConfig({
  plugins: [react(), basicSsl()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    // LAN phone testing uses nip.io hostnames (Google OAuth rejects bare-IP
    // origins); Vite blocks unknown Host headers unless allowed here.
    allowedHosts: ['.nip.io'],
    // Windows bind mounts don't deliver file-change events into the
    // container; poll so edits on the host are picked up.
    watch: {
      usePolling: true,
    },
    proxy: {
      // Browser calls http://localhost:5173/api/...; Vite forwards to the
      // backend service on the compose network.
      '/api': 'http://backend:8000',
    },
  },
})
