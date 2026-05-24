import { useEffect, useRef, useState } from 'react'
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
  const [model, setModel] = useState<string>('')
  const [messages, setMessages] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  // Default to the first served model once we know them.
  useEffect(() => {
    if (!model && models.data?.length) setModel(models.data[0])
  }, [models.data, model])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [messages])

  async function send() {
    const text = input.trim()
    if (!text || busy || !model) return
    setError(null)
    setInput('')

    const userMsg: Msg = { id: crypto.randomUUID(), role: 'user', content: text }
    const asstMsg: Msg = {
      id: crypto.randomUUID(),
      role: 'assistant',
      content: '',
      streaming: true,
    }
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
        // user stopped — keep whatever streamed so far
      } else if (err instanceof ApiError && err.status === 503) {
        setError('The pool has no compute workers online yet. Try again shortly.')
      } else if (err instanceof ApiError && err.status === 401) {
        setError('Your token was rejected. Reconnect with a valid invite.')
      } else {
        setError((err as Error).message)
      }
    } finally {
      setMessages((m) =>
        m.map((x) => (x.id === asstMsg.id ? { ...x, streaming: false } : x)),
      )
      setBusy(false)
      abortRef.current = null
    }
  }

  function stop() {
    abortRef.current?.abort()
  }

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-border px-6 py-3">
        <h1 className="text-sm font-medium">Chat</h1>
        <select
          value={model}
          onChange={(e) => setModel(e.target.value)}
          className="rounded-lg border border-border bg-panel-2 px-3 py-1.5 text-xs text-muted outline-none focus:border-accent"
        >
          {!models.data?.length && <option>loading models…</option>}
          {models.data?.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
      </header>

      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-2xl px-6 py-6">
          {messages.length === 0 && (
            <div className="mt-24 text-center text-muted">
              <div className="mx-auto mb-4 size-12 rounded-2xl bg-gradient-to-br from-accent to-accent-2" />
              <p className="text-lg text-text">Ask the pool anything</p>
              <p className="mt-1 text-sm">
                Your prompt runs across every Mac contributing right now.
              </p>
            </div>
          )}
          {messages.map((m) => (
            <Bubble key={m.id} msg={m} />
          ))}
          {error && (
            <div className="my-3 rounded-lg border border-bad/40 bg-bad/10 px-4 py-2 text-sm text-bad">
              {error}
            </div>
          )}
        </div>
      </div>

      <div className="border-t border-border px-6 py-4">
        <div className="mx-auto flex max-w-2xl items-end gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                send()
              }
            }}
            rows={1}
            placeholder="Message the pool…"
            className="max-h-40 flex-1 resize-none rounded-xl border border-border bg-panel-2 px-4 py-3 text-sm outline-none focus:border-accent"
          />
          {busy ? (
            <button
              onClick={stop}
              className="rounded-xl border border-border bg-panel-2 px-4 py-3 text-sm text-muted hover:text-text"
            >
              Stop
            </button>
          ) : (
            <button
              onClick={send}
              disabled={!input.trim() || !model}
              className="rounded-xl bg-gradient-to-br from-accent to-accent-2 px-5 py-3 text-sm font-medium text-white disabled:opacity-40"
            >
              Send
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

function Bubble({ msg }: { msg: Msg }) {
  const isUser = msg.role === 'user'
  return (
    <div className={`mb-5 flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-[85%] whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
          isUser
            ? 'bg-gradient-to-br from-accent to-accent-2 text-white'
            : 'bg-panel-2 text-text'
        }`}
      >
        {msg.content}
        {msg.streaming && !msg.content && <span className="text-muted">▍</span>}
      </div>
    </div>
  )
}
