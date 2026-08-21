import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
    allowedHosts: [
      'doorpost-smashing-regime.ngrok-free.dev',
      '.ngrok-free.dev',
      '.ngrok.io',
      'localhost'
    ],
    cors: {
      origin: [
        'https://doorpost-smashing-regime.ngrok-free.dev',
        'http://localhost:5173',
        'http://127.0.0.1:5173'
      ],
      credentials: true
    },
    headers: {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, PATCH, OPTIONS',
      'Access-Control-Allow-Headers': 'X-Requested-With, content-type, Authorization, ngrok-skip-browser-warning'
    },
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true
      },
      '/uploads': {
        target: 'http://localhost:8000',
        changeOrigin: true
      },
      '/exports': {
        target: 'http://localhost:8000',
        changeOrigin: true
      }
    }
  }
});
