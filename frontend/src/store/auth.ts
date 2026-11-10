import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'

interface AuthState {
  token: string | null
  username: string | null
  signIn: (token: string, username: string) => void
  signOut: () => void
}

// token 存 localStorage：刷新不掉登录。代价是 XSS 能读到它；
// 换成 httpOnly cookie 更安全，但需要后端改成从 cookie 取 token，目前不做
export const useAuth = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      username: null,
      signIn: (token, username) => set({ token, username }),
      signOut: () => set({ token: null, username: null }),
    }),
    { name: 'auth', storage: createJSONStorage(() => localStorage) },
  ),
)
