import { type FormEvent, useState } from 'react'

import { login, register } from '../api/auth'
import { errorMessage } from '../api/http'
import { useAuth } from '../store/auth'

type Mode = 'login' | 'register'

// 和后端 LoginRequest 的 Field(min_length=6, max_length=20) 保持一致
const valid = (v: string) => v.length >= 6 && v.length <= 20

export default function AuthPage() {
  const signIn = useAuth((s) => s.signIn)
  const [mode, setMode] = useState<Mode>('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!valid(username) || !valid(password)) {
      setError('用户名和密码都需要 6–20 位')
      return
    }
    setBusy(true)
    setError('')
    try {
      if (mode === 'register') await register(username, password)
      signIn(await login(username, password), username)
    } catch (err) {
      setError(errorMessage(err, mode === 'login' ? '登录失败，再试一次' : '注册失败，再试一次'))
    } finally {
      setBusy(false)
    }
  }

  function switchTo(next: Mode) {
    setMode(next)
    setError('')
  }

  const tab = (m: Mode, label: string) => (
    <button
      type="button"
      onClick={() => switchTo(m)}
      aria-pressed={mode === m}
      className={`pb-1.5 text-base transition-colors ${
        mode === m ? 'border-b-2 border-redpen font-semibold text-ink' : 'border-b-2 border-transparent text-pencil hover:text-ink'
      }`}
    >
      {label}
    </button>
  )

  return (
    <main className="flex min-h-full">
      <section className="grid-paper relative hidden flex-[1.1] flex-col justify-between border-r border-rule bg-paper-deep p-12 md:flex">
        <p className="font-serif text-2xl font-bold tracking-widest">学情伴学</p>
        <div>
          <h1 className="font-serif text-4xl leading-snug font-bold">
            会出题，
            <br />
            也记得你错在哪
          </h1>
          <p className="mt-5 max-w-sm leading-7 text-pencil">
            把题目发给它：出变式题、理清知识点、讲明白卡住的那一步。聊过的都留着，随时翻回去。
          </p>
        </div>
        <p className="w-fit -rotate-2 border-b-2 border-redpen pb-0.5 font-hand text-xl text-redpen">今天也来做两道题吧 ✓</p>
      </section>

      <section className="flex flex-1 items-center justify-center px-4 py-12">
        <form onSubmit={submit} className="w-full max-w-xs" noValidate>
          <p className="mb-10 font-serif text-2xl font-bold tracking-widest md:hidden">学情伴学</p>
          <div className="mb-8 flex gap-6">
            {tab('login', '登录')}
            {tab('register', '注册')}
          </div>

          <label className="mb-1.5 block text-sm text-pencil" htmlFor="username">
            用户名
          </label>
          <input
            id="username"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value.trim())}
            className="mb-5 w-full rounded-lg border border-rule bg-white/70 px-3 py-2.5 outline-none focus:border-ink"
          />

          <label className="mb-1.5 block text-sm text-pencil" htmlFor="password">
            密码
          </label>
          <input
            id="password"
            type="password"
            autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-lg border border-rule bg-white/70 px-3 py-2.5 outline-none focus:border-ink"
          />

          <p role="alert" className="mt-2 min-h-5 text-sm text-redpen">
            {error}
          </p>

          <button
            type="submit"
            disabled={busy}
            className="mt-4 w-full rounded-lg bg-ink py-2.5 font-semibold text-paper transition-opacity disabled:opacity-60"
          >
            {busy ? (mode === 'login' ? '登录中…' : '注册中…') : mode === 'login' ? '登录' : '注册并登录'}
          </button>
        </form>
      </section>
    </main>
  )
}
