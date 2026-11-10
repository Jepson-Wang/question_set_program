import { type KeyboardEvent, useEffect, useRef } from 'react'

interface Props {
  value: string
  onChange: (v: string) => void
  onSend: () => void
  busy: boolean
}

export default function Composer({ value, onChange, onSend, busy }: Props) {
  const ref = useRef<HTMLTextAreaElement>(null)

  // 跟着内容长高，最多到 10 行左右，再多就在框内滚动
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 240)}px`
  }, [value])

  const canSend = !busy && value.trim().length > 0

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    // isComposing：中文输入法选词时按的回车是「确认候选词」，不能当成发送
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      if (canSend) onSend()
    }
  }

  return (
    <div>
      <div className="flex items-end gap-3 rounded-xl border border-rule bg-white/80 px-4 py-3 focus-within:border-ink">
        <label htmlFor="composer" className="sr-only">
          输入题目或问题
        </label>
        <textarea
          id="composer"
          ref={ref}
          rows={1}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="输入题目或问题"
          className="max-h-60 flex-1 resize-none bg-transparent leading-6 outline-none placeholder:text-pencil/70"
        />
        <button
          type="button"
          onClick={onSend}
          disabled={!canSend}
          className="shrink-0 rounded-lg bg-redpen px-4 py-1.5 text-sm font-semibold text-white transition-opacity disabled:opacity-40"
        >
          {busy ? '作答中' : '发送'}
        </button>
      </div>
      <p className="mt-1.5 hidden text-right text-xs text-pencil md:block">Enter 发送，Shift + Enter 换行</p>
    </div>
  )
}
