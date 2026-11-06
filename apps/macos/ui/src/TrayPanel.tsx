// The menu bar dropdown: lend this Mac to a pool, set how much, and watch
// credits land. One orange dot in the menu bar; everything else is here.
import { useState, useSyncExternalStore } from 'react'
import { openMain } from './lib/bridge'
import { autosize } from './lib/autosize'
import { creditsOf, store, type BridgeState } from './lib/store'
import { fmt, shortModel } from './lib/format'
import { Icon } from './components/Icon'
import { Toggle } from './components/Toggle'

export function TrayPanel() {
  const s = useSyncExternalStore(store.subscribe, store.getSnapshot)
  const l = s.lend
  if (!l) {
    return (
      <div ref={autosize} className="panel w-[316px] rounded-[14px] p-6 text-center text-dim">
        Starting up
      </div>
    )
  }
  const credits = creditsOf(s.usage, s.config)
  const pool = s.local.lendingTo
    ? [...(s.catalog?.pools ?? []), ...s.local.pools].find((p) => p.id === s.local.lendingTo)?.name
    : s.config?.pool_name
  const status = statusLine(l.reason, pool ?? 'the pool', s)

  return (
    <div
      ref={autosize}
      className="panel w-[316px] overflow-hidden rounded-[14px] text-ink"
    >
      <section className="px-4 pb-3.5 pt-3.5">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-[11px] text-dim">This Mac</p>
            <p className="text-[14px] font-semibold leading-tight">
              {l.device.name} · {l.device.gb} GB
            </p>
          </div>
          <Toggle variant="accent" label="Lend this Mac" on={l.enabled} onChange={(v) => void store.lendSet({ enabled: v })} />
        </div>
        <div className="mt-3.5 flex items-start gap-2.5">
          <span
            className={`mt-[6px] size-2 shrink-0 rounded-full ${
              l.lending ? 'bg-signal' : 'border border-dim'
            }`}
          />
          <div>
            <p className="text-[12.5px] font-medium leading-tight">{status.title}</p>
            <p className="mt-0.5 text-[11.5px] text-dim">{status.body}</p>
          </div>
        </div>
      </section>

      <hr className="border-line" />
      <section className="px-4 py-3.5">
        <div className="flex items-baseline justify-between">
          <p className="label !text-[11px]">Lend up to</p>
          <MemoryValue key={l.gb} gb={l.gb} />
        </div>
        <MemorySlider
          total={l.device.gb}
          max={l.max_gb}
          value={l.gb}
          onCommit={(gb) => void store.lendSet({ gb })}
        />
        <p className="mt-1.5 text-right text-[11px] text-faint">{l.reserve_gb} GB always kept for your Mac</p>
        <div className="mt-3 space-y-2.5">
          <Row label="Only when plugged in" on={l.plugged_only} onChange={(v) => void store.lendSet({ plugged_only: v })} />
          <Row label="Pause while I'm using it" on={l.pause_in_use} onChange={(v) => void store.lendSet({ pause_in_use: v })} />
        </div>
      </section>

      <hr className="border-line" />
      <section className="flex items-start gap-3 px-4 py-3.5">
        <Icon name="credits" size={17} className="mt-1" />
        <div>
          <p className="relative text-[15px] font-bold leading-tight">
            <span key={credits.balance === null ? 'n' : Math.round(credits.balance)} className="fade">
              {credits.balance === null ? '—' : fmt(credits.balance)}
            </span>
            {s.creditPulse && Date.now() - s.creditPulse.at < 4000 && (
              <span key={s.creditPulse.at} className="pop absolute -top-1 left-14 text-[12px] font-semibold text-dim">
                +{s.creditPulse.amount}
              </span>
            )}
          </p>
          <p className="text-[11.5px] text-dim">
            credits
            {credits.perHour !== null && credits.perHour >= 0.5
              ? ` · earning about ${fmt(credits.perHour)} an hour`
              : ' · earns while you lend'}
          </p>
        </div>
      </section>

      <hr className="border-line" />
      <footer className="flex items-center justify-between px-4 py-3 text-[13px]">
        <button
          onClick={() => void store.lendSet({ pause_minutes: l.paused ? 0 : 60 })}
          className="text-[#d6d6d6] hover:text-ink"
        >
          {l.paused ? 'Resume' : 'Pause for an hour'}
        </button>
        <button onClick={() => void openMain()} className="text-dim hover:text-ink">
          Open dots
        </button>
      </footer>
    </div>
  )
}

function statusLine(reason: string, pool: string, s: BridgeState): { title: string; body: string } {
  switch (reason) {
    case 'lending':
      return {
        title: `Working for ${pool}`,
        body: s.account?.model ? `Serving ${shortModel(s.account.model)}` : 'Helping answer questions',
      }
    case 'paused':
      return { title: 'Paused', body: 'Resumes when you press Resume' }
    case 'unplugged':
      return { title: 'Waiting for power', body: 'Plug in to start lending' }
    case 'in_use':
      return { title: 'Paused while you use it', body: 'Resumes when you step away' }
    default:
      return { title: 'Not lending', body: 'Turn this on to lend this Mac' }
  }
}

function MemoryValue({ gb }: { gb: number }) {
  return <p className="fade text-[14px] font-bold">{gb} GB</p>
}

function Row({ label, on, onChange }: { label: string; on: boolean; onChange: (v: boolean) => void }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-[13px] text-[#d6d6d6]">{label}</span>
      <Toggle label={label} on={on} onChange={onChange} />
    </div>
  )
}

// A track spanning all of the Mac's memory: the lent amount fills, the share
// always kept for the owner is hatched and can't be dragged into.
function MemorySlider({
  total,
  max,
  value,
  onCommit,
}: {
  total: number
  max: number
  value: number
  onCommit: (gb: number) => void
}) {
  const [drag, setDrag] = useState<number | null>(null)
  const shown = drag ?? value
  const pct = (g: number) => `${(g / total) * 100}%`
  const at = (e: React.PointerEvent<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect()
    return Math.min(max, Math.max(1, Math.round(((e.clientX - r.left) / r.width) * total)))
  }
  return (
    <div
      className="relative mt-3 h-6 touch-none"
      onPointerDown={(e) => {
        e.currentTarget.setPointerCapture(e.pointerId)
        setDrag(at(e))
      }}
      onPointerMove={(e) => drag !== null && setDrag(at(e))}
      onPointerUp={(e) => {
        const g = at(e)
        setDrag(null)
        if (g !== value) onCommit(g)
      }}
    >
      <div className="absolute inset-x-0 top-1/2 h-[6px] -translate-y-1/2 overflow-hidden rounded-full bg-[#252525]">
        <div className="absolute inset-y-0 left-0 bg-[#d4d4d4]" style={{ width: pct(shown) }} />
        <div className="hatch absolute inset-y-0 right-0" style={{ width: `${100 - (max / total) * 100}%` }} />
      </div>
      <span
        className="absolute top-1/2 size-[18px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-white shadow-md"
        style={{ left: pct(shown) }}
      />
    </div>
  )
}
