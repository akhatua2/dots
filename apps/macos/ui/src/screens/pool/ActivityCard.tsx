import type { PoolDetail } from '../../lib/pools'
import { Card } from '../../ui/Card'
import { Icon } from '../../components/Icon'

export function ActivityCard({ activity }: { activity: PoolDetail['activity'] }) {
  return (
    <Card className="p-5">
      <p className="text-[14px] font-semibold">Activity</p>
      <ul className="mt-2 divide-y divide-line">
        {activity.map((item, i) => (
          <li key={i} className="flex items-center gap-3 py-2.5 text-[14px]">
            <span className="grid size-7 shrink-0 place-items-center rounded-full bg-surface-2 text-[11px] font-semibold">
              {item.who === '+' ? <Icon name="sparkle" size={13} /> : item.who}
            </span>
            <span className="flex-1">{item.text}</span>
            <span className="text-[13px] text-dim">{item.when}</span>
          </li>
        ))}
      </ul>
    </Card>
  )
}
