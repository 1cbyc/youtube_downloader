import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/video_info': {
        target: 'http://localhost:5001',
        changeOrigin: true,
      },
      '/download': {
        target: 'http://localhost:5001',
        changeOrigin: true,
      },
      '/queue': {
        target: 'http://localhost:5001',
        changeOrigin: true,
      },
      '/pause': {
        target: 'http://localhost:5001',
        changeOrigin: true,
      },
      '/resume': {
        target: 'http://localhost:5001',
        changeOrigin: true,
      },
      '/pause_all': {
        target: 'http://localhost:5001',
        changeOrigin: true,
      },
      '/resume_all': {
        target: 'http://localhost:5001',
        changeOrigin: true,
      },
      '/list_downloads': {
        target: 'http://localhost:5001',
        changeOrigin: true,
      },
      '/download_file': {
        target: 'http://localhost:5001',
        changeOrigin: true,
      },
      '/history': {
        target: 'http://localhost:5001',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://localhost:5001',
        changeOrigin: true,
      },
    },
  },
})
