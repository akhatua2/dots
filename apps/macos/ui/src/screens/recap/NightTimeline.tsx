import type { Recap } from '../../lib/pools'
import { Card } from '../../ui/Card'

/** Who lent when, as rows of dots along one night. */
export function NightTimeline({ night }: { night: Recap['night'] }) {
  return (
    <Card className="p-5">
      <p className="flex items-baseline justify-between">
        <span className="text-[14.5px] font-semibold">The night</span>
        <span className="text-[13px] text-dim">{night.rows.length} Macs</span>
      </p>
      <ul className="mt-3 space-y-3">
        {night.rows.map((row) => (
          <li key={row.who} className="grid grid-cols-[52px_1fr_64px] items-center gap-3 text-[14px]">
            <span className={row.mine ? 'font-semibold text-signal-text' : 'text-[#cfcfcf]'}>{row.who}</span>
            <span className="relative h-2">
              <span
                className={`dot-row absolute inset-y-0 ${row.mine ? 'text-signal' : 'text-[#9a9a9a]'}`}
                style={{ left: `${row.start * 100}%`, width: `${(row.end - row.start) * 100}%` }}
              />
            </span>
            <span className={`num text-right ${row.mine ? 'text-signal-text' : 'text-dim'}`}>{row.label}</span>
          </li>
        ))}
      </ul>
      <div className="mt-2 grid grid-cols-[52px_1fr_64px] gap-3 text-[12px] text-faint">
        <span />
        <span className="flex justify-between">
          {night.ticks.map((tick) => (
            <span key={tick}>{tick}</span>
          ))}
        </span>
        <span />
      </div>
    </Card>
  )
}
