import type { Step } from '../store/chat'

const CIRCLED = '①②③④⑤⑥⑦⑧⑨⑩'

interface Props {
  steps: Step[]
  running: boolean
}

/** 宽屏：回答右侧的红笔页边批注 */
export function MarginNotes({ steps, running }: Props) {
  return (
    <aside aria-label="思考过程" className="border-l border-redpen/25 pl-4 font-hand text-[15px] leading-7 text-redpen">
      <ol>
        {steps.map((s, i) => (
          <li key={i} className="note-in">
            {CIRCLED[i] ?? `${i + 1}.`} {s.text}
          </li>
        ))}
      </ol>
      {running ? (
        <p className="text-redpen/60">
          {steps.length ? '' : '读题中'}
          <span className="caret">…</span>
        </p>
      ) : (
        steps.length > 0 && <p className="note-in">答完了 ✓</p>
      )}
    </aside>
  )
}

/** 窄屏放不下页边，收成回答上方一行，点开看步骤 */
export function InlineNotes({ steps, running }: Props) {
  const last = steps[steps.length - 1]?.text
  const summary = running ? (last ? `${last}…` : '读题中…') : `想了 ${steps.length} 步`
  return (
    <details className="mb-3 font-hand text-[15px] text-redpen">
      <summary className="cursor-pointer select-none">{summary}</summary>
      <ol className="mt-1 border-l border-redpen/25 pl-3 leading-7">
        {steps.map((s, i) => (
          <li key={i}>
            {CIRCLED[i] ?? `${i + 1}.`} {s.text}
          </li>
        ))}
      </ol>
    </details>
  )
}
