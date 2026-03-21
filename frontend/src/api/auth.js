import request from '../utils/request'

// 登录接口
export function loginApi(data) {
  return request.post('/login', data)
}

// 注册接口
export function registerApi(data) {
  return request.post('/register', data)
}