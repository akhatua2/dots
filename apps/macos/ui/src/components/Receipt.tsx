// "Which Macs answered, why it ran there, what it cost."
import type { Receipt as R } from '../lib/bridge'
import { shortModel } from '../lib/pools'
import { Avatar } from './Avatar'
import { Icon } from './Icon'

export function Receipt({ r }: { r: R }) {
  const macs = r.macs.length
  return (
    <div className="mt-3 overflow-hidden rounded-xl bg-[#141414]">
      <div className="flex items-center gap-3 px-3.5 py-3">
        <div className="flex -space-x-1.5">
          {r.macs.slice(0, 4).map((m, i) => (
            <Avatar key={i} letter={(m.name[0] || '?').toUpperCase()} size={26} ring={m.ready} />
          ))}
        </div>
        <div className="min-w-0 flex-1">
          <p className="truncate text-[13px] font-medium text-ink">
            Ran on {r.pool}
            {r.model_id ? ` · ${shortModel(r.model_id)}` : ''}
          </p>
          <p className="text-[12px] text-dim">
            {macs} {macs === 1 ? 'Mac' : 'Macs'}
            {typeof r.tps === 'number' ? ` · ${r.tps} tok/s` : ''}
          </p>
        </div>
        {r.free && <span className="rounded-full bg-surface-2 px-2.5 py-1 text-[12px] text-ink">Free for you</span>}
        {r.private && (
          <span className="flex items-center gap-1.5 rounded-full bg-surface-2 px-2.5 py-1 text-[12px] text-ink">
            <Icon name="lock" size={12} /> Private
          </span>
        )}
      </div>
      <div className="border-t border-line px-3.5 py-3">
        <p className="label mb-2.5 !text-[10px]">Why this route</p>
        <ul className="space-y-2">
          {r.reasons.map((x, i) => (
            <li key={i} className="flex items-start gap-2.5 text-[12.5px] text-dim">
              <Icon name={x.icon} size={14} className="mt-0.5" />
              {x.text}
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}
