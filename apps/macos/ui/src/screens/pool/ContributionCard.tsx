import { useState } from 'react'
import { Toggle } from '../../components/Toggle'
import { Card } from '../../ui/Card'

interface Props {
  enabled: boolean
  lending: boolean
  gb: number
  maxGb: number
  poolGb: number
  layers: string | null
  onToggle: (on: boolean) => void
  onCommit: (gb: number) => void
  onLeave: () => void
}

const TICKS = 64

/** "Your Contribution": the on/off switch, how much you lend, and where your share sits in the pool. */
export function ContributionCard({ enabled, lending, gb, maxGb, poolGb, layers, onToggle, onCommit, onLeave }: Props) {
  const [drag, setDrag] = useState<number | null>(null)
  const shown = drag ?? gb
  const mineTicks = Math.max(1, Math.round((shown / Math.max(poolGb, 1)) * TICKS))

  const valueAt = (e: React.PointerEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    return Math.min(maxGb, Math.max(1, Math.round(((e.clientX - rect.left) / rect.width) * maxGb)))
  }

  return (
    <Card className="p-5">
      <div className="flex items-center justify-between">
        <p className="text-[14px] font-semibold">Your Contribution</p>
        <Toggle variant="accent" on={enabled} onChange={onToggle} label="Lend to this pool" />
      </div>
      <p className="mt-2 flex items-baseline gap-2 text-[34px] font-semibold leading-none tracking-[-0.03em]">
        {shown} GB
        <span className="flex items-center gap-1.5 text-[13.5px] font-normal tracking-normal text-dim">
          {lending && <span className="size-1.5 rounded-full bg-signal" />}
          {lending ? 'lending now' : 'not lending'}
        </span>
      </p>

      <div
        className="relative mt-4 h-6 touch-none"
        onPointerDown={(e) => {
          e.currentTarget.setPointerCapture(e.pointerId)
          setDrag(valueAt(e))
        }}
        onPointerMove={(e) => drag !== null && setDrag(valueAt(e))}
        onPointerUp={(e) => {
          const next = valueAt(e)
          setDrag(null)
          if (next !== gb) onCommit(next)
        }}
      >
        <span className="absolute inset-x-0 top-1/2 h-[3px] -translate-y-1/2 rounded-full bg-surface-2" />
        <span className="absolute left-0 top-1/2 h-[3px] -translate-y-1/2 rounded-full bg-signal" style={{ width: `${(shown / maxGb) * 100}%` }} />
        <span
          className="absolute top-1/2 size-[18px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-white shadow"
          style={{ left: `${(shown / maxGb) * 100}%` }}
        />
      </div>

      <div className="mt-2 flex gap-[2px]" aria-hidden>
        {Array.from({ length: TICKS }, (_, i) => (
          <span key={i} className={`h-[6px] flex-1 ${i >= TICKS - mineTicks ? 'bg-signal' : 'bg-line-2'}`} />
        ))}
      </div>

      <div className="mt-3 flex items-center justify-between text-[13px] text-dim">
        <span>{layers ?? 'Waiting for layers'}</span>
        <button onClick={onLeave} className="hover:text-ink">
          Leave Pool…
        </button>
      </div>
    </Card>
  )
}
