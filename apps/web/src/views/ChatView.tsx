import { useCallback, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { listModels, streamChat, ApiError, type ChatMessage } from '../lib/api'
import type { Conn } from '../lib/config'

interface Msg extends ChatMessage {
  id: string
  streaming?: boolean
}

export function ChatView({ conn }: { conn: Conn }) {
  const models = useQuery({
    queryKey: ['models', conn.api],
    queryFn: ({ signal }) => listModels(conn, signal),
  })
  const model = models.data?.[0] ?? ''
  const [messages, setMessages] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  async function send() {
    const text = input.trim()
    if (!text || busy || !model) return
    setError(null)
    setInput('')

    const userMsg: Msg = { id: crypto.randomUUID(), role: 'user', content: text }
    const asstMsg: Msg = { id: crypto.randomUUID(), role: 'assistant', content: '', streaming: true }
    const history = [...messages, userMsg]
    setMessages([...history, asstMsg])
    setBusy(true)

    const ctrl = new AbortController()
    abortRef.current = ctrl
    try {
      await streamChat(conn, {
        model,
        messages: history.map(({ role, content }) => ({ role, content })),
        signal: ctrl.signal,
        onToken: (delta) =>
          setMessages((m) =>
            m.map((x) => (x.id === asstMsg.id ? { ...x, content: x.content + delta } : x)),
          ),
      })
    } catch (err) {
      if ((err as Error).name === 'AbortError') {
        /* keep partial */
      } else if (err instanceof ApiError && err.status === 503) {
        setError('No compute nodes online yet.')
      } else {
        setError((err as Error).message)
      }
    } finally {
      setMessages((m) => m.map((x) => (x.id === asstMsg.id ? { ...x, streaming: false } : x)))
      setBusy(false)
      abortRef.current = null
    }
  }

  const composer = (
    <Composer
      value={input}
      onChange={setInput}
      onSend={send}
      onStop={() => abortRef.current?.abort()}
      busy={busy}
      disabled={!model}
    />
  )

  // Empty: a single centered input. Active: transcript + input pinned bottom.
  if (messages.length === 0) {
    return (
      <div className="flex min-h-0 flex-1 flex-col items-center justify-center px-6">
        <div className="w-full max-w-2xl">{composer}</div>
      </div>
    )
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex-1 overflow-y-auto">
        <div ref={followGrowth} className="mx-auto max-w-2xl space-y-6 px-6 py-8">
          {messages.map((m) => (
            <Entry key={m.id} msg={m} />
          ))}
          {error && <div className="font-mono text-[12px] text-warn">{error}</div>}
        </div>
      </div>
      <div className="px-6 pb-5">
        <div className="mx-auto max-w-2xl">{composer}</div>
      </div>
    </div>
  )
}

/** Keeps the transcript scrolled to the bottom as it grows, a new message or a streaming token alike. */
function followGrowth(content: HTMLDivElement | null) {
  const scroller = content?.parentElement
  if (!content || !scroller) return
  const observer = new ResizeObserver(() => scroller.scrollTo({ top: scroller.scrollHeight, behavior: 'smooth' }))
  observer.observe(content)
  return () => observer.disconnect()
}

function Entry({ msg }: { msg: Msg }) {
  if (msg.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] whitespace-pre-wrap rounded-3xl bg-surface-2 px-4 py-2.5 font-sans text-[15px] leading-relaxed text-ink">
          {msg.content}
        </div>
      </div>
    )
  }
  return (
    <div className="whitespace-pre-wrap font-sans text-[15px] leading-relaxed text-ink">
      {msg.content}
      {msg.streaming && <span className="cursor" />}
    </div>
  )
}

function Composer({
  value,
  onChange,
  onSend,
  onStop,
  busy,
  disabled,
}: {
  value: string
  onChange: (v: string) => void
  onSend: () => void
  onStop: () => void
  busy: boolean
  disabled: boolean
}) {
  // Grows with what is typed, up to 200 px. A new callback per value makes React run it after each change,
  // including when the box is emptied after sending.
  const fit = useCallback(
    (el: HTMLTextAreaElement | null) => {
      if (!el) return
      el.style.height = '0px'
      el.style.height = Math.min(el.scrollHeight, 200) + 'px'
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [value],
  )

  return (
    <div className="rounded-3xl border border-line-2 bg-bg p-2 transition-shadow focus-within:border-faint">
      <textarea
        ref={fit}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault()
            onSend()
          }
        }}
        rows={1}
        placeholder="Message the pool"
        className="max-h-52 w-full resize-none bg-transparent px-3 py-2 font-sans text-[15px] text-ink outline-none placeholder:text-faint"
      />
      <div className="flex justify-end pt-1 pr-1">
        <button
          onClick={busy ? onStop : onSend}
          disabled={!busy && (!value.trim() || disabled)}
          className="grid size-8 shrink-0 place-items-center rounded-full bg-signal text-bg transition-opacity hover:opacity-90 disabled:bg-line disabled:text-faint"
        >
          {busy ? '■' : '↑'}
        </button>
      </div>
    </div>
  )
}
