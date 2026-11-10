import axios, { isAxiosError } from 'axios'

import { useAuth } from '../store/auth'

export const http = axios.create({ timeout: 15000 })

http.interceptors.request.use((config) => {
  const token = useAuth.getState().token
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// token 过期或失效：清掉登录态，路由守卫会把人送回登录页
http.interceptors.response.use(
  (res) => res,
  (err) => {
    if (isAxiosError(err) && err.response?.status === 401 && useAuth.getState().token) {
      useAuth.getState().signOut()
    }
    return Promise.reject(err)
  },
)

/** 把后端报错翻成一句给人看的话。FastAPI 的 detail 可能是字符串，也可能是校验错误数组 */
export function errorMessage(err: unknown, fallback: string): string {
  if (isAxiosError(err)) {
    if (!err.response) return '连不上服务器，检查网络或稍后再试'
    const detail = err.response.data?.detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail) && detail[0]?.msg) return detail[0].msg
  }
  if (err instanceof Error && err.message) return err.message
  return fallback
}
