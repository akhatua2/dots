import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Msg } from '../lib/store'
import { Icon } from './Icon'
import { Receipt } from './Receipt'

export function Answer({ m }: { m: Msg }) {
  const choosing = m.pending && (m.text === '' || m.text === 'Choosing where to run it')
  return (
    <div>
      {choosing ? (
        <p className="text-[14px] text-dim">
          Choosing where to run it<span className="cursor" />
        </p>
      ) : (
        <div className="prose-dots select-text text-[15px] leading-relaxed text-[#e4e4e4]">
          <Markdown remarkPlugins={[remarkGfm]}>{m.text}</Markdown>
          {m.pending && <span className="cursor" />}
        </div>
      )}
      {!m.pending && m.receipt && <Receipt r={m.receipt} />}
      {!m.pending && m.receipt && <SecondOpinion />}
    </div>
  )
}

// Frontier models arrive with credits; until the router can reach them this
// stays visible but unavailable.
function SecondOpinion() {
  return (
    <div className="mt-3 flex items-center justify-between rounded-xl border border-line px-3.5 py-3">
      <div>
        <p className="text-[12.5px] text-dim">Want a second opinion?</p>
        <p className="mt-0.5 flex items-center gap-2 text-[13px] text-dim">
          <Icon name="external" size={13} /> A frontier model leaves your pools. Coming with credits.
        </p>
      </div>
      <button disabled className="rounded-lg bg-btn px-3.5 py-2 text-[13px] font-medium text-faint">
        Ask Claude
      </button>
    </div>
  )
}
