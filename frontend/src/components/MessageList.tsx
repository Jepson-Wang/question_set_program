import type { UiMessage } from '../store/chat'
import Answer from './Answer'
import { InlineNotes, MarginNotes } from './MarginNotes'

interface Props {
  messages: UiMessage[]
  onRetry: (text: string) => void
}

function AssistantMessage({ m, question, onRetry }: { m: UiMessage; question?: string; onRetry: Props['onRetry'] }) {
  const running = m.status === 'running'
  const hasNotes = m.steps !== undefined

  return (
    <div className={hasNotes ? 'lg:grid lg:grid-cols-[minmax(0,1fr)_11rem] lg:gap-8' : ''}>
      <div className="min-w-0">
        {hasNotes && (
          <div className="lg:hidden">
            <InlineNotes steps={m.steps!} running={running} />
          </div>
        )}
        {m.content && (
          <div className={hasNotes ? 'answer-in' : ''}>
            <Answer text={m.content} />
          </div>
        )}
        {running && !m.content && <p className="text-pencil">正在作答，稍等一下…</p>}
        {m.status === 'error' && (
          <p className="text-redpen">
            {m.error}
            {question && (
              <button type="button" onClick={() => onRetry(question)} className="ml-3 underline underline-offset-4">
                重新提问
              </button>
            )}
          </p>
        )}
      </div>
      {hasNotes && (
        <div className="hidden lg:block">
          <MarginNotes steps={m.steps!} running={running} />
        </div>
      )}
    </div>
  )
}

export default function MessageList({ messages, onRetry }: Props) {
  return (
    <ol className="flex flex-col gap-8">
      {messages.map((m, i) => (
        <li key={m.key} className={m.role === 'user' ? 'flex justify-end' : ''}>
          {m.role === 'user' ? (
            <p className="max-w-[80%] rounded-2xl rounded-br-sm bg-ink px-4 py-2.5 leading-7 whitespace-pre-wrap text-paper">
              {m.content}
            </p>
          ) : (
            <AssistantMessage m={m} question={messages[i - 1]?.role === 'user' ? messages[i - 1].content : undefined} onRetry={onRetry} />
          )}
        </li>
      ))}
    </ol>
  )
}
