import { useState } from 'react'
import { store, type BridgeState } from '../../lib/store'
import { Answer } from '../../components/Answer'
import { Icon } from '../../components/Icon'

/**
 * The Ask surface: a question, the answer, and the receipt. One component, two
 * homes: inline in the main window, and the floating panel for Option-Space
 * from other apps. `onClose` is what Esc does once there is nothing to clear.
 */
export function AskSurface({ s, onClose, hint }: { s: BridgeState; onClose?: () => void; hint: string }) {
  const answer = s.msgs[s.msgs.length - 1]
  const asked = s.msgs.length > 0

  return (
    <div className="panel overflow-hidden rounded-[20px] text-ink">
      {/* Re-keyed on every open so the field is empty and focused. */}
      <QuestionRow key={s.focusTick} s={s} onClose={onClose} />
      {asked && answer?.role === 'assistant' && (
        <div className="max-h-[540px] overflow-y-auto border-t border-line px-5 py-4">
          <Answer m={answer} s={s} />
        </div>
      )}
      <footer className="flex items-center justify-between border-t border-line px-5 py-2.5 text-[12.5px] text-dim">
        <span className="flex items-center gap-4">
          <Key name="return" label="ask" />
          <Key name="esc" label={hint} />
        </span>
        <Icon name="gear" size={14} />
      </footer>
    </div>
  )
}

function Key({ name, label }: { name: string; label: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <kbd className="rounded bg-surface-2 px-1.5 py-0.5 font-sans text-[11.5px] font-semibold text-[#cfcfcf]">{name}</kbd>
      {label}
    </span>
  )
}

function QuestionRow({ s, onClose }: { s: BridgeState; onClose?: () => void }) {
  const [input, setInput] = useState('')
  const asked = s.msgs.length > 0

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter') {
      if (input.trim() && !s.busy && s.phase === 'ready' && !asked) store.send(input)
    } else if (e.key === 'Escape') {
      if (asked) store.newQuestion()
      else onClose?.()
    }
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
        spellCheck={false}
        value={asked ? s.msgs[0].text : input}
        readOnly={asked}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder={s.phase === 'ready' ? 'Ask anything. Drop a text file here for context.' : 'Connecting to the pool'}
        className="min-w-0 flex-1 bg-transparent text-[17px] placeholder:text-faint"
      />
      <span className="flex shrink-0 items-center gap-1 rounded-full bg-signal-soft px-3 py-1 text-[13px] font-medium text-signal-text">
        Auto <Icon name="chevron" size={13} />
      </span>
    </div>
  )
}
