import { getInviteLink } from '../../lib/bridge'
import { fmt } from '../../lib/format'
import { Button } from '../../ui/Button'
import { Card } from '../../ui/Card'

interface Props {
  level: number
  nextModel: string | null
  capacityGb: number
  totalGb: number
  toGo: number
  mineGb: number
  canLendMore: boolean
  onLendMore: () => void
}

/** How close the pool is to its next model, split by who is carrying it. */
export function LevelCard({ level, nextModel, capacityGb, totalGb, toGo, mineGb, canLendMore, onLendMore }: Props) {
  const others = Math.max(0, capacityGb - mineGb)
  const pct = (gb: number) => `${Math.min(100, (gb / Math.max(totalGb, 1)) * 100)}%`

  return (
    <Card className="p-5">
      <p className="flex items-baseline justify-between text-[14px]">
        <span className="font-semibold">Level {level}</span>
        {nextModel && (
          <span className="text-[13px] text-dim">
            {fmt(capacityGb)} of {fmt(totalGb)} GB
          </span>
        )}
      </p>

      {nextModel ? (
        <>
          <p className="mt-3 text-[44px] font-semibold leading-none tracking-[-0.03em]">
            {fmt(toGo)} GB <span className="text-[16px] font-normal text-dim">to go</span>
          </p>
          <p className="mt-3 text-[14px] text-[#cfcfcf]">Unlock {nextModel} and keep hard prompts here, free.</p>
          <div className="mt-3 flex h-[3px] gap-px overflow-hidden rounded-full bg-surface-2">
            <span className="bg-[#7a7a7a]" style={{ width: pct(others * 0.7) }} />
            <span className="bg-[#cfcfcf]" style={{ width: pct(others * 0.3) }} />
            <span className="bg-signal" style={{ width: pct(mineGb) }} />
          </div>
          <div className="mt-4 flex items-center gap-4">
            <Button variant="primary" disabled={!canLendMore} onClick={onLendMore}>
              Lend {Math.min(toGo, 99)} GB More
            </Button>
            <Button variant="ghost" onClick={() => void getInviteLink().then((l) => navigator.clipboard.writeText(l))}>
              Bring a Mac
            </Button>
          </div>
        </>
      ) : (
        <p className="mt-3 text-[14px] text-[#cfcfcf]">This pool runs everything it knows about.</p>
      )}
    </Card>
  )
}
