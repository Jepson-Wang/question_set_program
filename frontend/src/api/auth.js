import request from '../utils/request'
//登录api

export function loginApi(data) {
  return request.post('/login/login', data)
}
//请求api
export function registerApi(data) {
  return request.post('/login/register', data)
}