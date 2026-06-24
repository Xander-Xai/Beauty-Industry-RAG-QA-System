import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// API 代理目标 — 优先使用 VITE_API_PROXY_TARGET 环境变量，默认 localhost:8000
const API_PROXY_TARGET = process.env.VITE_API_PROXY_TARGET || 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/api': API_PROXY_TARGET,
    },
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
  },
})