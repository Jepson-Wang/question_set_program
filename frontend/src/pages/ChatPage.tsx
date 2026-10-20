import { isAxiosError } from 'axios'
import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import { errorMessage } from '../api/http'
import Composer from '../components/Composer'
import MessageList from '../components/MessageList'
import Sidebar from '../components/Sidebar'
import { useChat } from '../store/chat'

const EXAMPLES = [
  '帮我出两道和这题类似的题：解方程 x² − 5x + 6 = 0',
  '勾股定理有哪些要记住的知识点？',
  '我之前哪类题最容易错？',
]

function parseId(raw: string | undefined): number | null {
  const n = Number(raw)
  return raw && Number.isInteger(n) && n > 0 ? n : null
}

export default function ChatPage() {
  const params = useParams()
  const sessionId = parseId(params.sessionId)
  const navigate = useNavigate()

  const { sessions, sessionsLoaded, streaming, loadSessions, loadMessages, startSession, send } = useChat()
  const messages = useChat((s) => (sessionId ? s.messages[sessionId] : undefined))

  const [draft, setDraft] = useState('')
  const [notice, setNotice] = useState('')
  const [drawerOpen, setDrawerOpen] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!sessionsLoaded) loadSessions().catch((err) => setNotice(errorMessage(err, '会话列表加载失败，刷新页面再试')))
  }, [sessionsLoaded, loadSessions])

  // 地址栏里的 id 不合法（比如 /c/abc），回到新对话
  useEffect(() => {
    if (params.sessionId && sessionId === null) navigate('/', { replace: true })
  }, [params.sessionId, sessionId, navigate])

  useEffect(() => {
    if (sessionId === null) return
    loadMessages(sessionId).catch((err) => {
      if (isAxiosError(err) && err.response?.status === 404) {
        navigate('/', { replace: true })
        setNotice('没有找到这个对话，已为你打开新对话')
      } else {
        setNotice(errorMessage(err, '对话加载失败，刷新页面再试'))
      }
    })
  }, [sessionId, loadMessages, navigate])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'end' })
  }, [messages])

  const busy = sessionId !== null && streaming.has(sessionId)
  const title = sessions.find((s) => s.id === sessionId)?.title ?? '新对话'

  async function submit(text: string) {
    const question = text.trim()
    if (!question || busy) return
    setDraft('')
    setNotice('')
    let id = sessionId
    if (id === null) {
      try {
        id = await startSession()
      } catch (err) {
        setDraft(question)
        setNotice(errorMessage(err, '新对话创建失败，再试一次'))
        return
      }
      navigate(`/c/${id}`)
    }
    send(id, question)
  }

  const empty = sessionId === null || (messages !== undefined && messages.length === 0)

  return (
    <div className="flex h-full">
      {/* 宽屏常驻侧边栏，窄屏做成抽屉 */}
      <div className="hidden md:block">
        <Sidebar sessions={sessions} loaded={sessionsLoaded} activeId={sessionId} streaming={streaming} onNavigate={() => {}} />
      </div>
      {drawerOpen && (
        <div className="fixed inset-0 z-20 flex md:hidden">
          <Sidebar
            sessions={sessions}
            loaded={sessionsLoaded}
            activeId={sessionId}
            streaming={streaming}
            onNavigate={() => setDrawerOpen(false)}
          />
          <button type="button" aria-label="关闭会话列表" className="flex-1 bg-ink/30" onClick={() => setDrawerOpen(false)} />
        </div>
      )}

      <main className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-3 border-b border-rule px-4 py-3 md:hidden">
          <button
            type="button"
            onClick={() => setDrawerOpen(true)}
            className="shrink-0 text-sm whitespace-nowrap text-pencil"
            aria-label="打开会话列表"
          >
            ☰ 会话
          </button>
          <span className="truncate font-serif font-semibold">{title}</span>
        </header>

        {notice && (
          <p role="status" className="border-b border-redpen/20 bg-redpen/5 px-6 py-2 text-sm text-redpen">
            {notice}
          </p>
        )}

        <div className="flex-1 overflow-y-auto">
          {empty ? (
            <div className="grid-paper flex h-full flex-col items-center justify-center px-4">
              <div className="w-full max-w-xl">
                <h1 className="font-serif text-3xl font-bold">今天想练点什么？</h1>
                <p className="mt-3 text-pencil">把题目打出来，或者直接问。也可以从下面挑一个开始。</p>
                <ul className="mt-6 flex flex-col gap-2">
                  {EXAMPLES.map((q) => (
                    <li key={q}>
                      <button
                        type="button"
                        onClick={() => setDraft(q)}
                        className="w-full rounded-lg border border-rule bg-paper px-4 py-2.5 text-left transition-colors hover:border-ink"
                      >
                        {q}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          ) : (
            <div className="mx-auto w-full max-w-4xl px-4 py-8 md:px-10">
              {messages ? <MessageList messages={messages} onRetry={submit} /> : <p className="text-pencil">加载对话中…</p>}
              <div ref={bottomRef} />
            </div>
          )}
        </div>

        <div className="mx-auto w-full max-w-4xl px-4 pt-2 pb-5 md:px-10">
          <Composer value={draft} onChange={setDraft} onSend={() => submit(draft)} busy={busy} />
        </div>
      </main>
    </div>
  )
}
