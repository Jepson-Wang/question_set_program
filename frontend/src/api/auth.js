import request from '../utils/request'
//登录api

// 登录接口
export function loginApi(data) {
  return request.post('/login', data)
}
<<<<<<< HEAD

// 注册接口
=======
//请求api
>>>>>>> main
export function registerApi(data) {
  return request.post('/register', data)
}