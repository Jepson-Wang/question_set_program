import { useEffect } from 'react'
import { BrowserRouter, Navigate, Outlet, Route, Routes } from 'react-router-dom'

import AuthPage from './pages/AuthPage'
import ChatPage from './pages/ChatPage'
import { useAuth } from './store/auth'
import { useChat } from './store/chat'

function RequireAuth() {
  const token = useAuth((s) => s.token)
  return token ? <Outlet /> : <Navigate to="/auth" replace />
}

function GuestOnly() {
  const token = useAuth((s) => s.token)
  return token ? <Navigate to="/" replace /> : <Outlet />
}

export default function App() {
  // 退出登录（包括 token 过期被动退出）时清掉上一个人的会话缓存
  useEffect(() => useAuth.subscribe((s, prev) => prev.token && !s.token && useChat.getState().reset()), [])

  // 路由不能以 /login、/sessions、/agent 开头：开发时这些前缀被代理到后端，刷新会打到接口上
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<GuestOnly />}>
          <Route path="/auth" element={<AuthPage />} />
        </Route>
        <Route element={<RequireAuth />}>
          <Route path="/" element={<ChatPage />} />
          <Route path="/c/:sessionId" element={<ChatPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
