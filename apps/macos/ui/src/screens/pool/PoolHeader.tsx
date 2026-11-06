import { useState } from 'react'
import { getInviteLink } from '../../lib/bridge'
import { store } from '../../lib/store'
import type { Pool } from '../../lib/pools'
import { Avatar } from '../../components/Avatar'
import { Icon } from '../../components/Icon'
import { Button } from '../../ui/Button'

/** Pool name, its members (ringed in orange while they are lending) and the invite button. */
export function PoolHeader({ pool, level, iAmLive }: { pool: Pool; level: number; iAmLive: boolean }) {
  const [copied, setCopied] = useState(false)
  const members = pool.detail?.members ?? pool.lenders.filter((l) => l !== 'You').map((name) => ({ name, live: true }))

  async function invite() {
    await navigator.clipboard.writeText(await getInviteLink())
    setCopied(true)
    setTimeout(() => setCopied(false), 1800)
  }

  return (
    <header className="flex items-start justify-between">
      <div>
        <h1 className="text-[26px] font-bold leading-[1.15] tracking-[-0.025em]">{pool.name}</h1>
        <p className="mt-0.5 text-[13.5px] text-dim">
          {pool.model} · Level {level}
        </p>
      </div>
      <div className="flex items-center gap-2.5">
        <span className="flex items-center gap-2">
          {members.slice(0, 5).map((m) => (
            <Avatar key={m.name} letter={m.name} size={38} ring={m.live} />
          ))}
          {iAmLive && <Avatar letter="You" size={32} />}
        </span>
        <Button variant="secondary" className="flex items-center gap-1.5 border border-line-2 !bg-transparent" onClick={() => void invite()}>
          <Icon name="plus" size={14} strokeWidth={2.4} /> {copied ? 'Copied' : 'Invite'}
        </Button>
        <button
          onClick={() => store.nav({ name: 'settings' })}
          aria-label="More"
          className="grid size-8 place-items-center rounded-lg text-dim hover:text-ink"
        >
          <Icon name="more" size={18} />
        </button>
      </div>
    </header>
  )
}
