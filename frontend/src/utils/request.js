import axios from 'axios'
import { getToken, getTokenType, clearAuth } from './auth'

// 创建 axios 实例
const request = axios.create({
  // 后端基础地址
  baseURL: 'http://localhost:8000',
  // 超时时间
  timeout: 10000
})

// 请求拦截器
request.interceptors.request.use(
  (config) => {
    // 获取本地保存的 token 和 token_type
    const token = getToken()
    const tokenType = getTokenType()

    // 如果有 token，就自动带到请求头里
    if (token) {
      // 如果后端返回了 token_type，就优先用它
      // 没有的话，默认按 Bearer 处理
      config.headers.Authorization = `${tokenType || 'Bearer'} ${token}`
    }

    return config
  },
  (error) => {
    return Promise.reject(error)
  }
)

// 响应拦截器
request.interceptors.response.use(
  (response) => {
    // 直接返回后端数据
    return response.data
  },
  (error) => {
    // 如果未登录或 token 失效
    if (error.response?.status === 401) {
      clearAuth()
      window.location.href = '/login'
    }

    return Promise.reject(error)
  }
)

export default request