import type { RecentRequest } from '../../lib/bridge'
import { Card } from '../../ui/Card'
import { SectionLabel } from '../../ui/SectionLabel'

export function RecentRequests({ rows }: { rows: RecentRequest[] }) {
  return (
    <section className="mt-7">
      <SectionLabel>Recent requests</SectionLabel>
      <Card className="mt-3 divide-y divide-line">
        {rows.map((row, i) => (
          <div key={i} className="px-[18px] py-2.5 text-[14px]">
            <div className="grid grid-cols-[72px_1fr_1.4fr_1.1fr_84px_96px] items-center gap-3">
              <span className="num text-dim">{row.at}</span>
              <span className="font-semibold">{row.key}</span>
              <span className="truncate text-[#cfcfcf]">{row.ran}</span>
              <span className="num text-dim">{row.tokens}</span>
              <span className="num text-right font-semibold">{row.cost}</span>
              <span className="num text-right text-dim">{row.saved}</span>
            </div>
            {row.note && <p className="mt-0.5 pl-[84px] text-[13px] text-signal-text">{row.note}</p>}
          </div>
        ))}
        {rows.length === 0 && <p className="px-[18px] py-6 text-center text-[14px] text-dim">Requests from your apps show up here.</p>}
      </Card>
    </section>
  )
}
