import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// 膜总控台前端：vite dev 时浏览器访问，fetch 绝对地址 127.0.0.1:8931（后端已加 CORS）。
// 生产 pywebview 加载 dist/index.html 时同样走绝对地址，无需 proxy。
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: './', // 相对路径，pywebview 从 file:// 加载 dist 也能找到资源
  server: {
    port: 5173,
  },
})
