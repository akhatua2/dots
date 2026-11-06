import { store } from '../../lib/store'
import type { Pool } from '../../lib/pools'
import { PoolMark } from '../../components/Fingerprint'
import { Button } from '../../ui/Button'
import { Card } from '../../ui/Card'

/** "Sam invited you to Late Night Lab" — sits above the public pools. */
export function InviteBanner({ from, text, pool }: { from: string; text: string; pool: Pool }) {
  return (
    <Card className="mt-5 flex items-center gap-4 px-4 py-3.5">
      <PoolMark seed={pool.id} size={44} />
      <div className="min-w-0 flex-1">
        <p className="text-[15.5px] font-semibold leading-tight">
          {from} invited you to {pool.name}
        </p>
        <p className="mt-0.5 text-[14px] text-signal-text">{text}</p>
      </div>
      <Button variant="primary" onClick={() => store.nav({ name: 'invite', id: pool.id })}>
        Open
      </Button>
    </Card>
  )
}
