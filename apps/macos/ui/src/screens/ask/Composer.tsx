import { useState } from 'react'
import { store, type BridgeState } from '../../lib/store'
import { Icon } from '../../components/Icon'

/** The message box: grows with what you type, Enter sends, Shift+Enter adds a line. */
export function Composer({ s, placeholder }: { s: BridgeState; placeholder: string }) {
  const [text, setText] = useState('')
  const canSend = text.trim() !== '' && !s.busy && s.phase === 'ready'

  function send() {
    if (!canSend) return
    store.send(text)
    setText('')
  }

  return (
    <div className="panel rounded-[22px] p-3">
      {s.attachment && (
        <span className="mb-2 ml-1 inline-flex items-center gap-1.5 rounded-lg bg-surface-2 px-2.5 py-1 text-[13px]">
          <Icon name="file" size={13} />
          {s.attachment.name}
          <button onClick={() => store.clearAttachment()} aria-label="Remove file" className="text-dim hover:text-ink">
            <Icon name="x" size={12} />
          </button>
        </span>
      )}
      <textarea
        autoFocus
        rows={1}
        spellCheck={false}
        value={text}
        placeholder={placeholder}
        onChange={(e) => {
          setText(e.target.value)
          e.target.style.height = '0px'
          e.target.style.height = `${Math.min(e.target.scrollHeight, 220)}px`
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault()
            send()
          }
        }}
        className="block max-h-[220px] w-full resize-none bg-transparent px-2 py-1.5 text-[15.5px] leading-relaxed placeholder:text-faint"
      />
      <div className="mt-1 flex items-center justify-between">
        <span className="flex items-center gap-1 rounded-full bg-signal-soft px-3 py-1 text-[13px] font-medium text-signal-text">
          Auto <Icon name="chevron" size={13} />
        </span>
        <button
          onClick={send}
          disabled={!canSend}
          aria-label="Send"
          className="grid size-8 place-items-center rounded-full bg-signal text-[#1a0a03] transition-colors disabled:bg-btn disabled:text-faint"
        >
          <Icon name="arrowUp" size={16} strokeWidth={2.4} />
        </button>
      </div>
    </div>
  )
}
