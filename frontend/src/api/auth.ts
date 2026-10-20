import { http } from './http'

export async function login(username: string, password: string): Promise<string> {
  const { data } = await http.post<{ access_token: string }>('/login/login', { username, password })
  return data.access_token
}

export async function register(username: string, password: string): Promise<void> {
  await http.post('/login/register', { username, password })
}
