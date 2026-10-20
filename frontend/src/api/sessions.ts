import { http } from './http'

export interface Session {
  id: number
  title: string
  created_at: string
  updated_at: string
}

export interface StoredMessage {
  id: number
  role: 'user' | 'assistant'
  content: string
  created_at: string
}

export async function createSession(): Promise<Session> {
  return (await http.post<Session>('/sessions')).data
}

export async function listSessions(): Promise<Session[]> {
  return (await http.get<Session[]>('/sessions')).data
}

export async function listMessages(sessionId: number): Promise<StoredMessage[]> {
  return (await http.get<StoredMessage[]>(`/sessions/${sessionId}/messages`)).data
}
