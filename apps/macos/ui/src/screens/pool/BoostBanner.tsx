import { store } from '../../lib/store'
import type { Boost } from '../../lib/pools'
import { Avatar } from '../../components/Avatar'
import { Button } from '../../ui/Button'
import { Card } from '../../ui/Card'

/** The next planned boost for a pool, with a one-click way to join in. */
export function BoostBanner({ poolId, boost, counted }: { poolId: string; boost: Boost; counted: boolean }) {
  const toGo = Math.max(0, boost.goal_gb - boost.pledged_gb)
  const share = Math.min(1, boost.pledged_gb / boost.goal_gb)

  return (
    <Card className="mt-5 flex items-center gap-4 px-4 py-3">
      <CalendarTile day={boost.day} date={boost.date} />
      <div className="min-w-0 flex-1">
        <p className="text-[15.5px] font-semibold leading-tight">{boost.title}</p>
        <p className="mt-0.5 flex items-center gap-3 text-[13.5px] text-dim">
          Saturday, {boost.window}
          <span className="h-1 w-[76px] overflow-hidden rounded-full bg-surface-2">
            <span className="block h-full rounded-full bg-[#cfcfcf]" style={{ width: `${share * 100}%` }} />
          </span>
          {toGo} GB to go
        </p>
      </div>
      <span className="flex -space-x-2">
        {boost.boosters.map((b) => (
          <Avatar key={b.name} letter={b.name} size={24} />
        ))}
      </span>
      <span className="text-[13.5px] text-dim">{boost.boosters.length} boosting</span>
      <Button variant="outline" onClick={() => store.nav({ name: 'boost', id: poolId })}>
        {counted ? 'Boosting ✓' : 'Count Me In'}
      </Button>
    </Card>
  )
}

export function CalendarTile({ day, date, size = 'sm' }: { day: string; date: string; size?: 'sm' | 'lg' }) {
  const big = size === 'lg'
  return (
    <span className={`overflow-hidden rounded-lg border border-line-2 bg-surface-2 text-center ${big ? 'w-[62px]' : 'w-[46px]'}`}>
      <span className={`block bg-signal font-bold tracking-wider text-[#1a0a03] ${big ? 'py-0.5 text-[12px]' : 'text-[10px] leading-[14px]'}`}>
        {day}
      </span>
      <span className={`block font-semibold ${big ? 'py-1 text-[26px]' : 'text-[19px] leading-[26px]'}`}>{date}</span>
    </span>
  )
}
