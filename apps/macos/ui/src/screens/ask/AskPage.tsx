import { store, type BridgeState, type Msg } from '../../lib/store'
import { Answer } from '../../components/Answer'
import { Icon } from '../../components/Icon'
import { Button } from '../../ui/Button'
import { Composer } from './Composer'

/**
 * Ask in the main window, laid out like a chat: a centred column, the
 * conversation scrolling above, the message box fixed below. With nothing
 * asked yet the box sits in the middle of the page.
 */
export function AskPage({ s }: { s: BridgeState }) {
  const started = s.msgs.length > 0
  const notReady = s.phase === 'ready' ? 'Ask anything. Drop a text file here for context.' : 'Connecting to the pool'

  if (!started) {
    return (
      <div className="flex h-full flex-col items-center justify-center px-8 pb-16">
        <div className="w-full max-w-[680px]">
          <h1 className="mb-6 text-center text-[30px] font-semibold tracking-[-0.03em]">What can the pool help with?</h1>
          <Composer key={s.focusTick} s={s} placeholder={notReady} />
          <p className="mt-3 text-center text-[13px] text-dim">
            {s.phase === 'error' ? `Couldn't connect: ${s.error}` : 'Answers run in your pools first, and every one comes with a receipt.'}
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col">
      <header className="flex justify-end pl-6 pr-14 pt-3.5">
        <Button variant="ghost" className="flex items-center gap-1.5 px-2 py-1" onClick={() => store.newQuestion()}>
          <Icon name="plus" size={14} /> New chat
        </Button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-[680px] space-y-8 px-8 pb-6 pt-4">
          {s.msgs.map((m, i) => (
            <Turn key={i} m={m} s={s} />
          ))}
          {/* Runs on every render, so the view follows the newest token. */}
          <div ref={(el) => el?.scrollIntoView({ block: 'end' })} />
        </div>
      </div>

      <div className="mx-auto w-full max-w-[680px] px-8 pb-6">
        <Composer s={s} placeholder="Reply" />
      </div>
    </div>
  )
}

function Turn({ m, s }: { m: Msg; s: BridgeState }) {
  if (m.role === 'assistant') return <Answer m={m} s={s} />
  return (
    <div className="flex justify-end">
      <div className="max-w-[82%] select-text whitespace-pre-wrap rounded-[20px] bg-surface-2 px-4 py-2.5 text-[15px] leading-relaxed">
        {m.attachment && (
          <p className="mb-1 flex items-center gap-1.5 text-[12.5px] text-dim">
            <Icon name="file" size={13} /> {m.attachment}
          </p>
        )}
        {m.text}
      </div>
    </div>
  )
}
