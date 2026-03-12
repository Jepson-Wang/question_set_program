import axios from 'axios'

const request = axios.create({
  baseURL: 'http://localhost:8000',
  timeout: 10000
})

request.interceptors.response.use(
  response => {
    return response.data
  },
  error => {
    // 如果后端返回了错误信息，比如 401，但有响应体，就把它也交回前端处理
    if (error.response && error.response.data) {
      return Promise.resolve(error.response.data)
    }

    return Promise.reject(error)
  }
)

export default request