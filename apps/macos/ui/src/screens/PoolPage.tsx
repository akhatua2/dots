import { store, type BridgeState } from '../lib/store'
import { poolView, runnable } from '../lib/pools'
import { Avatar } from '../components/Avatar'
import { Icon } from '../components/Icon'
import { Swirl } from '../components/Swirl'

export function PoolPage({ s, id }: { s: BridgeState; id: string }) {
  const raw = [...(s.catalog?.pools ?? []), ...s.local.pools].find((p) => p.id === id)
  if (!raw) return <p className="p-10 text-dim">That pool is gone.</p>
  const joined = s.local.lendingTo === id
  const lentGb = s.lend?.gb ?? 0
  const p = poolView(raw, joined, lentGb)
  const endorsed = s.local.endorsed.includes(id)
  const models = runnable(p)
  const next = models.find((m) => !m.unlocked)
  const unlocked = joined ? models.find((m) => m.unlock_on_join && m.unlocked) : undefined
  const device = s.lend?.device.name ?? 'Mac'
  const toGo = next ? next.at_gb - p.capacity_gb : 0
  const progress = next ? Math.max(0.04, Math.min(1, p.capacity_gb / next.at_gb)) : 1

  return (
    <div className="px-[34px] pb-9 pt-[38px]">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Swirl seed={p.id} size={64} cells={32} />
          <div>
            <h1 className="text-[26px] font-bold leading-[1.15] tracking-[-0.025em]">{p.name}</h1>
            <p className="text-[13.5px] text-dim">
              {p.public ? 'Public pool' : 'Private pool'} · {p.model} · {p.macs} {p.macs === 1 ? 'Mac' : 'Macs'}
              {p.capacity_gb > 0 ? ` · ${p.capacity_gb} GB` : ''}
            </p>
          </div>
        </div>
        {p.public && p.endorsements > 0 && (
          <button
            onClick={() => store.toggleEndorse(id)}
            className={`flex items-center gap-2 rounded-[10px] px-3.5 py-2 text-[14px] font-semibold ${
              endorsed ? 'bg-btn text-ink' : 'bg-signal text-[#1a0a03]'
            }`}
          >
            <Icon name="shield" size={16} /> {endorsed ? 'Endorsed' : 'Endorse'}
          </button>
        )}
      </div>

      {unlocked && (
        <div className="card mt-5 flex items-center justify-between px-[18px] py-[17px] text-[15.5px]">
          <p className="flex items-center gap-3 font-medium">
            <span className="size-2 rounded-full bg-signal" />
            Your {device} unlocked {unlocked.name} here tonight.
          </p>
          <p className="text-[13.5px] text-dim">
            It took the pool from {raw.capacity_gb} to {p.capacity_gb} GB
          </p>
        </div>
      )}

      <p className="label mt-[26px] !text-[12px]">Macs lending now · {p.lenders.length}</p>
      <div className="mt-3 flex flex-wrap gap-2">
        {p.lenders.map((l, i) => (
          <Avatar key={i} letter={l} size={34} className="!text-[13px]" />
        ))}
      </div>

      <p className="label mt-[26px] !text-[12px]">What it can run</p>
      <div className="mt-3 space-y-2.5">
        {models.map((m) => (
          <div key={m.name} className="card flex items-center gap-4 px-[18px] py-[14px]">
            <span className="grid w-5 place-items-center text-ink">
              <Icon name={m.unlocked ? 'check' : 'lock'} size={17} className={m.unlocked ? '' : 'text-dim'} />
            </span>
            <span className={`w-[150px] text-[15px] font-semibold ${m.unlocked ? '' : 'text-[#cfcfcf]'}`}>
              {m.name}
            </span>
            {m.unlocked ? (
              <span className="text-[14.5px] text-[#cfcfcf]">
                {m.at_gb === 0
                  ? 'Running now'
                  : `${m.unlock_on_join && joined ? 'Unlocked tonight' : 'Unlocked'} at ${m.at_gb} GB`}
              </span>
            ) : (
              <>
                <span className="text-[14.5px] text-[#cfcfcf]">{toGo} GB to go</span>
                <span className="ml-auto h-1.5 w-[28%] overflow-hidden rounded-full bg-surface-2">
                  <span className="block h-full rounded-full bg-[#8c8c8c]" style={{ width: `${progress * 100}%` }} />
                </span>
              </>
            )}
          </div>
        ))}
      </div>

      {p.public && p.endorsements > 0 && (
        <>
          <p className="mt-[22px] flex items-center gap-2 text-[14.5px]">
            <Icon name="shield" size={17} className="text-dim" />
            {p.endorsements + (endorsed ? 1 : 0)} endorsements
          </p>
          {endorsed && joined && (
            <p className="mt-3 inline-flex items-center gap-2 rounded-full border border-line-2 px-3.5 py-2 text-[14px] font-medium">
              <Icon name="shield" size={16} /> You&apos;re an early backer. It&apos;s on your profile.
            </p>
          )}
        </>
      )}

      {!joined && p.public && (
        <button
          onClick={() => store.join(p.id)}
          className="mt-8 rounded-xl bg-signal px-5 py-3 text-[15px] font-semibold text-[#1a0a03]"
        >
          Add my Mac
        </button>
      )}
    </div>
  )
}
