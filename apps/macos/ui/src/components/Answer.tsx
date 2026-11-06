import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { store, type BridgeState, type Msg } from '../lib/store'
import { Icon } from './Icon'
import { Receipt } from './Receipt'

export function Answer({ m, s }: { m: Msg; s: BridgeState }) {
  const choosing = m.pending && (m.text === '' || m.text === 'Choosing where to run it')
  const pools = (s.catalog?.pools ?? []).filter((p) => s.local.joined.includes(p.id))
  const frontier = s.config?.frontier
  return (
    <div>
      {m.frontier && (
        <p className="mb-1.5 text-[13px]">
          <b className="font-semibold">{m.frontier.label}</b> <span className="text-dim">via dots</span>
        </p>
      )}
      {choosing ? (
        <div className="flex items-center gap-6 text-[13px] text-dim">
          <span>
            Choosing where to run it<span className="cursor" />
          </span>
          {pools.slice(0, 2).map((p) => (
            <span key={p.id} className="font-medium text-[#cfcfcf]">
              {p.name}
            </span>
          ))}
          {frontier?.enabled && <span className="font-medium text-[#cfcfcf]">{frontier.label}</span>}
        </div>
      ) : (
        <div className="prose-dots select-text text-[14.5px] leading-relaxed text-[#e4e4e4]">
          <Markdown remarkPlugins={[remarkGfm]}>{m.text}</Markdown>
          {m.pending && <span className="cursor" />}
        </div>
      )}
      {!m.pending && m.receipt && <Receipt r={m.receipt} />}
      {!m.pending && m.frontier && (
        <div className="mt-3 flex items-center gap-2.5 rounded-xl bg-[#141414] px-3.5 py-3 text-[13px]">
          <Icon name="external" size={14} />
          <span className="font-medium">Ran at Anthropic · {m.frontier.label}</span>
          <span className="text-dim">{m.frontier.tokens.toLocaleString('en-US')} tokens</span>
        </div>
      )}
      {!m.pending && m.receipt && frontier && (
        <div className="mt-3 flex items-center justify-between rounded-xl border border-line px-3.5 py-3">
          <div>
            <p className="text-[12px] text-dim">Want a second opinion?</p>
            <p className="mt-0.5 text-[14px] font-semibold">{frontier.label}</p>
            <p className="mt-0.5 flex items-center gap-2 text-[12px] text-dim">
              <Icon name="external" size={12} /> Leaves your pools and goes to Anthropic
            </p>
          </div>
          <button
            disabled={!frontier.enabled || s.busy}
            onClick={() => void store.askClaude()}
            className="rounded-lg bg-btn px-3.5 py-2 text-[13px] font-semibold disabled:text-faint"
          >
            {frontier.enabled ? 'Ask Claude' : 'Add API key'}
          </button>
        </div>
      )}
    </div>
  )
}
