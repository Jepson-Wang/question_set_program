import { Link } from 'react-router-dom'

import type { Session } from '../api/sessions'
import { useAuth } from '../store/auth'

interface Props {
  sessions: Session[]
  loaded: boolean
  activeId: number | null
  streaming: Set<number>
  onNavigate: () => void
}

function groupByDay(sessions: Session[]) {
  const startOfToday = new Date()
  startOfToday.setHours(0, 0, 0, 0)
  const startOfYesterday = new Date(startOfToday)
  startOfYesterday.setDate(startOfYesterday.getDate() - 1)

  const groups: { label: string; items: Session[] }[] = [
    { label: '今天', items: [] },
    { label: '昨天', items: [] },
    { label: '更早', items: [] },
  ]
  for (const s of sessions) {
    const t = new Date(s.updated_at)
    groups[t >= startOfToday ? 0 : t >= startOfYesterday ? 1 : 2].items.push(s)
  }
  return groups.filter((g) => g.items.length)
}

export default function Sidebar({ sessions, loaded, activeId, streaming, onNavigate }: Props) {
  const { username, signOut } = useAuth()

  return (
    <nav aria-label="会话" className="flex h-full w-64 flex-col border-r border-rule bg-paper-deep">
      <p className="px-5 pt-5 pb-4 font-serif text-lg font-bold tracking-widest">学情伴学</p>

      <div className="px-3">
        <Link
          to="/"
          onClick={onNavigate}
          className="block rounded-lg border border-dashed border-pencil/50 px-3 py-2 text-sm text-pencil transition-colors hover:border-ink hover:text-ink"
        >
          ＋ 新对话
        </Link>
      </div>

      <div className="mt-3 flex-1 overflow-y-auto px-3 pb-4">
        {!loaded && <p className="px-3 py-2 text-sm text-pencil">加载会话中…</p>}
        {loaded && sessions.length === 0 && (
          <p className="px-3 py-2 text-sm leading-6 text-pencil">还没有对话。发出第一个问题后，它会出现在这里。</p>
        )}
        {groupByDay(sessions).map((g) => (
          <section key={g.label} className="mt-3">
            <h2 className="px-3 pb-1 text-xs text-pencil">{g.label}</h2>
            <ul>
              {g.items.map((s) => {
                const active = s.id === activeId
                return (
                  <li key={s.id}>
                    <Link
                      to={`/c/${s.id}`}
                      onClick={onNavigate}
                      aria-current={active ? 'page' : undefined}
                      className={`flex items-center gap-2 rounded-r-md border-l-[3px] px-3 py-2 text-sm transition-colors ${
                        active
                          ? 'border-redpen bg-paper font-semibold text-ink'
                          : 'border-transparent text-ink/75 hover:bg-paper/60 hover:text-ink'
                      }`}
                    >
                      <span className="truncate">{s.title}</span>
                      {streaming.has(s.id) && (
                        <span className="ml-auto shrink-0 font-hand text-xs text-redpen">作答中</span>
                      )}
                    </Link>
                  </li>
                )
              })}
            </ul>
          </section>
        ))}
      </div>

      <div className="flex items-center justify-between border-t border-rule px-5 py-3 text-sm">
        <span className="truncate text-pencil">{username}</span>
        <button type="button" onClick={signOut} className="shrink-0 text-pencil hover:text-redpen">
          退出登录
        </button>
      </div>
    </nav>
  )
}
