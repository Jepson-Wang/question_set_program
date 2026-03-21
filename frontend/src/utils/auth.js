// 保存 access_token
export function setToken(token) {
  localStorage.setItem('access_token', token)
}

// 获取 access_token
export function getToken() {
  return localStorage.getItem('access_token')
}

// 删除 access_token
export function removeToken() {
  localStorage.removeItem('access_token')
}

// 保存 token_type
export function setTokenType(tokenType) {
  localStorage.setItem('token_type', tokenType)
}

// 获取 token_type
export function getTokenType() {
  return localStorage.getItem('token_type')
}

// 删除 token_type
export function removeTokenType() {
  localStorage.removeItem('token_type')
}

// 一次性清掉登录信息
export function clearAuth() {
  localStorage.removeItem('access_token')
  localStorage.removeItem('token_type')
}