// Option-Space: a panel over whatever you are reading. A router decides where
// each question runs, and every answer comes with a receipt.
import { useState, useSyncExternalStore } from 'react'
import { getCurrentWindow } from '@tauri-apps/api/window'
import { autosize } from './lib/autosize'
import { store } from './lib/store'
import { Answer } from './components/Answer'
import { Icon } from './components/Icon'

export function AskPanel() {
  const s = useSyncExternalStore(store.subscribe, store.getSnapshot)
  const answer = s.msgs[s.msgs.length - 1]
  const asked = s.msgs.length > 0

  return (
    <div
      ref={autosize}
      className="w-[620px] overflow-hidden rounded-[20px] border border-line bg-[#1e1e1e] text-ink shadow-2xl"
    >
      {/* Re-keyed on every open so the field is empty and focused. */}
      <AskInput key={s.focusTick} />
      {asked && answer?.role === 'assistant' && (
        <div className="max-h-[520px] overflow-y-auto border-t border-line px-5 py-4">
          <Answer m={answer} s={s} />
        </div>
      )}
    </div>
  )
}

function AskInput() {
  const s = useSyncExternalStore(store.subscribe, store.getSnapshot)
  const [input, setInput] = useState('')
  const asked = s.msgs.length > 0

  function send() {
    if (!input.trim() || s.busy || s.phase !== 'ready' || asked) return
    store.send(input)
  }

  return (
    <div className="flex items-center gap-3 px-5 py-4">
      <span className="size-2 shrink-0 rounded-full bg-signal" />
      {s.attachment && (
        <span className="flex shrink-0 items-center gap-1.5 rounded-lg bg-surface-2 px-2.5 py-1 text-[13px]">
          <Icon name="file" size={13} />
          {s.attachment.name}
          <button onClick={() => store.clearAttachment()} aria-label="Remove file" className="text-dim hover:text-ink">
            <Icon name="x" size={12} />
          </button>
        </span>
      )}
      <input
        autoFocus
        value={input}
        readOnly={asked}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') send()
          if (e.key === 'Escape') void getCurrentWindow().hide()
        }}
        placeholder={
          s.phase === 'ready' ? 'Ask anything. Drop a text file here for context.' : 'Connecting to the pool'
        }
        className="min-w-0 flex-1 bg-transparent text-[16px] outline-none placeholder:text-faint"
      />
      <span className="flex shrink-0 items-center gap-1 rounded-full bg-signal-soft px-3 py-1 text-[13px] font-medium text-signal-text">
        Auto <Icon name="chevron" size={13} />
      </span>
    </div>
  )
}
