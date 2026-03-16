import request from '../utils/request'

export function loginApi(data) {
  return request.post('/login/login', data)
}

export function registerApi(data) {
  return request.post('/login/register', data)
}