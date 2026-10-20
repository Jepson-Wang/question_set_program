import { create } from 'zustand'

import { errorMessage } from '../api/http'
import { createSession, listMessages, listSessions, type Session } from '../api/sessions'
import { type AgentEvent, streamAnalyse } from '../api/stream'

/** 页边批注里的一步。历史消息没有这些（后端不存思考过程），只有刚刚发生的对话才有 */
export interface Step {
  text: string
}

export interface UiMessage {
  key: string
  role: 'user' | 'assistant'
  content: string
  steps?: Step[]
  status?: 'running' | 'done' | 'error'
  error?: string
}

interface ChatState {
  sessions: Session[]
  sessionsLoaded: boolean
  /** 按会话缓存消息。流式回答写回发起时的那个会话，用户中途切走也不会写错地方 */
  messages: Record<number, UiMessage[]>
  /** 正在生成回答的会话；同一个会话一次只能问一个问题 */
  streaming: Set<number>
  loadSessions: () => Promise<void>
  loadMessages: (sessionId: number) => Promise<void>
  /** 新对话在发第一条消息时才建会话，避免侧边栏堆满空会话 */
  startSession: () => Promise<number>
  send: (sessionId: number, text: string) => Promise<void>
  reset: () => void
}

const TOOL_LABELS: Record<string, string> = {
  common_tool: '直接回答',
  extract_tool: '提取知识点',
  query_memory_tool: '翻看之前的对话',
  question_set_tool: '生成变式题',
  user_profile_save_tool: '记下你的学习情况',
  user_profile_query_tool: '查看你的学习档案',
  user_profile_delete_tool: '更新学习档案',
  load_skill_tool: '翻出出题方法',
}

// 只把「决定调用哪个工具」记成一步；工具返回（skill_loaded / observation）对学生来说
// 是重复信息，记进去批注就成了「翻出出题方法、已加载出题方法、拿到结果」这种流水账
function toStep(e: AgentEvent): Step | null {
  return e.type === 'thinking' ? { text: TOOL_LABELS[e.action] ?? e.action } : null
}

let keySeq = 0
const nextKey = () => `local-${++keySeq}`

const initial = { sessions: [], sessionsLoaded: false, messages: {}, streaming: new Set<number>() }

export const useChat = create<ChatState>()((set, get) => {
  /** 只改某个会话的最后一条消息（即正在生成的助手回答） */
  const patchLast = (sessionId: number, patch: (m: UiMessage) => UiMessage) =>
    set((s) => {
      const list = s.messages[sessionId] ?? []
      if (!list.length) return s
      return { messages: { ...s.messages, [sessionId]: [...list.slice(0, -1), patch(list[list.length - 1])] } }
    })

  return {
    ...initial,

    async loadSessions() {
      const sessions = await listSessions()
      set({ sessions, sessionsLoaded: true })
    },

    async loadMessages(sessionId) {
      // 已经在内存里的（包括正在生成的）不重新拉，免得把进行中的回答覆盖掉
      if (get().messages[sessionId]) return
      const stored = await listMessages(sessionId)
      const ui: UiMessage[] = stored.map((m) => ({ key: `db-${m.id}`, role: m.role, content: m.content, status: 'done' }))
      set((s) => ({ messages: { ...s.messages, [sessionId]: s.messages[sessionId] ?? ui } }))
    },

    async startSession() {
      const created = await createSession()
      set((s) => ({ sessions: [created, ...s.sessions], messages: { ...s.messages, [created.id]: [] } }))
      return created.id
    },

    async send(sid, text) {
      set((s) => ({
        streaming: new Set(s.streaming).add(sid),
        messages: {
          ...s.messages,
          [sid]: [
            ...(s.messages[sid] ?? []),
            { key: nextKey(), role: 'user', content: text },
            { key: nextKey(), role: 'assistant', content: '', steps: [], status: 'running' },
          ],
        },
      }))

      try {
        await streamAnalyse(sid, text, (e) => {
          if (e.type === 'result') {
            patchLast(sid, (m) => ({ ...m, content: e.content }))
            return
          }
          const step = toStep(e)
          if (step) patchLast(sid, (m) => ({ ...m, steps: [...(m.steps ?? []), step] }))
        })
        patchLast(sid, (m) =>
          m.content ? { ...m, status: 'done' } : { ...m, status: 'error', error: '这次没有生成回答，换个说法再问一次' },
        )
      } catch (err) {
        patchLast(sid, (m) => ({ ...m, status: 'error', error: errorMessage(err, '回答失败了，再试一次') }))
      } finally {
        set((s) => {
          const streaming = new Set(s.streaming)
          streaming.delete(sid)
          return { streaming }
        })
        // 首条提问会在后端生成标题、刷新活跃时间，重新拉一次列表拿到准确的排序和标题
        get().loadSessions().catch(() => {})
      }
    },

    reset: () => set({ ...initial, streaming: new Set() }),
  }
})
