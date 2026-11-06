import type { ApiExtra } from '../../lib/pools'
import { SectionLabel } from '../../ui/SectionLabel'

/** Where this month's requests ran, as one bar and a legend. */
export function RequestSplit({ split }: { split: ApiExtra['split'] }) {
  return (
    <section className="mt-7">
      <SectionLabel>Where your requests ran this month</SectionLabel>
      <div className="mt-3 flex h-2.5 gap-0.5 overflow-hidden rounded-full">
        {split
          .filter((part) => part.pct > 0)
          .map((part) => (
            <span key={part.label} style={{ width: `${part.pct}%`, background: part.shade }} />
          ))}
      </div>
      <ul className="mt-3 flex flex-wrap gap-x-7 gap-y-1 text-[14px]">
        {split.map((part) => (
          <li key={part.label} className="flex items-center gap-2">
            <span className="size-2.5 rounded-sm" style={{ background: part.shade }} />
            <b className="font-semibold">{part.label}</b>
            <span className="num text-dim">
              {part.pct}% · {part.note}
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}
