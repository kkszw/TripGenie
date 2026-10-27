import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'path'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src')
    }
  },
  server: {
    port: 5173,
    proxy: {
      // ✅ 代理所有 /api 请求到后端
      '/api': {
        target: 'http://localhost:8000',  // 后端地址
        changeOrigin: true,
      }
    }
  }
})