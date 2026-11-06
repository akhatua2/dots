import { useState } from 'react'
import { store, type BridgeState } from '../lib/store'
import { Answer } from '../components/Answer'
import { Icon } from '../components/Icon'

export function AskView({ s }: { s: BridgeState }) {
  const [input, setInput] = useState('')
  function send() {
    if (!input.trim() || s.busy || s.phase !== 'ready') return
    store.send(input)
    setInput('')
  }

  const composer = (
    <div className="flex items-center gap-3 rounded-2xl border border-line bg-card px-4 py-3">
      <span className="size-2 shrink-0 rounded-full bg-signal" />
      <input
        value={input}
        autoFocus
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && send()}
        placeholder={s.phase === 'ready' ? 'Ask anything' : 'Connecting to the pool'}
        className="min-w-0 flex-1 bg-transparent text-[16px] outline-none placeholder:text-faint"
      />
      <span className="flex items-center gap-1 rounded-full bg-signal-soft px-3 py-1 text-[13px] font-medium text-signal-text">
        Auto <Icon name="chevron" size={13} />
      </span>
      <button
        onClick={send}
        disabled={s.busy || !input.trim() || s.phase !== 'ready'}
        aria-label="Send"
        className="grid size-8 place-items-center rounded-full bg-signal text-[#1a0a03] disabled:bg-btn disabled:text-faint"
      >
        <Icon name="arrowUp" size={16} strokeWidth={2.4} />
      </button>
    </div>
  )

  if (s.msgs.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center px-10">
        <div className="w-full max-w-[640px]">
          <h1 className="mb-5 text-center text-[30px] font-bold tracking-tight">Ask the pool</h1>
          {composer}
          <p className="mt-4 text-center text-[13.5px] text-dim">
            {s.phase === 'error'
              ? `Couldn't connect: ${s.error}`
              : 'Press ⌥ Space anywhere to ask without opening dots.'}
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-[680px] space-y-7 px-8 py-10">
          {s.msgs.map((m, i) =>
            m.role === 'user' ? (
              <div key={i} className="flex justify-end">
                <div className="max-w-[80%] select-text whitespace-pre-wrap rounded-2xl bg-surface-2 px-4 py-2.5 text-[15px]">
                  {m.attachment && (
                    <p className="mb-1 flex items-center gap-1.5 text-[12.5px] text-dim">
                      <Icon name="file" size={13} /> {m.attachment}
                    </p>
                  )}
                  {m.text}
                </div>
              </div>
            ) : (
              <Answer key={i} m={m} />
            ),
          )}
          <div ref={(el) => el?.scrollIntoView({ block: 'end' })} />
        </div>
      </div>
      <div className="mx-auto w-full max-w-[680px] px-8 pb-6">{composer}</div>
    </div>
  )
}
