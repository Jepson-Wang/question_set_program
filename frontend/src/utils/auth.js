// 专门管理 token 的工具文件

// 保存 token
export function setToken(token) {
  localStorage.setItem('token', token)
}

// 获取 token
export function getToken() {
  return localStorage.getItem('token')
}

// 删除 token
export function removeToken() {
  localStorage.removeItem('token')
}