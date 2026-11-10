import { fetchEventSource } from '@microsoft/fetch-event-source'

import { useAuth } from '../store/auth'

/** 后端 _stream_generator 推送的四种事件，见 backend/api/user_api/agent_api.py */
export type AgentEvent =
  | { type: 'thinking'; action: string; thought: string }
  | { type: 'skill_loaded'; content: string }
  | { type: 'observation'; content: string }
  | { type: 'result'; content: string }

export class StreamError extends Error {}

// 抛出这个类型就是告诉 fetchEventSource「别重试」：它默认会在出错后自动重连，
// 而这里是 POST 提问，重连等于把同一个问题再问一遍
class Fatal extends Error {}

export async function streamAnalyse(
  sessionId: number,
  text: string,
  onEvent: (e: AgentEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const token = useAuth.getState().token
  try {
    await fetchEventSource('/agent/analyse/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify({ session_id: sessionId, text }),
      signal,
      // 默认切到后台标签页会断开、切回来再重新请求——对 POST 来说就是重复提问
      openWhenHidden: true,
      async onopen(res) {
        if (res.ok) return
        if (res.status === 401) useAuth.getState().signOut()
        const detail = await res.json().then((b) => b?.detail, () => undefined)
        throw new Fatal(typeof detail === 'string' ? detail : `请求失败（${res.status}）`)
      },
      onmessage(msg) {
        if (msg.data === '[DONE]') return
        onEvent(JSON.parse(msg.data) as AgentEvent)
      },
      onclose() {
        // 正常结束时服务端会先发 [DONE] 再关；这里不区分，交给调用方看有没有拿到 result
      },
      onerror(err) {
        throw err instanceof Fatal ? err : new Fatal('连接中断了，回答可能没有生成完')
      },
    })
  } catch (err) {
    if (err instanceof Fatal) throw new StreamError(err.message)
    throw err
  }
}
