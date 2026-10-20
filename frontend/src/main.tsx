import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
// 字体和公式的 CSS 里引用了相对路径的字体文件，必须从 JS 引入让 Vite 改写路径；
// 放进 index.css 用 @import 会被 Tailwind 内联，路径就指错了
import 'katex/dist/katex.min.css'
import 'lxgw-wenkai-webfont/lxgwwenkai-regular.css'
import './index.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
