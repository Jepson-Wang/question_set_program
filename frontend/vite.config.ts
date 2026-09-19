import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // 开发时把后端接口代理到本地 uvicorn，前端代码里一律写相对路径（/login/login、
    // /agent/analyse），这样就不用为开发和生产各配一套 baseURL，也绕开了跨域
    proxy: {
      '/login': 'http://127.0.0.1:8000',
      '/agent': 'http://127.0.0.1:8000',
      '/health': 'http://127.0.0.1:8000',
    },
  },
})
