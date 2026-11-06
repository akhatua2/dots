import { useState } from 'react'
import { creditsOf, store, type BridgeState, type Route } from '../lib/store'
import { fmt, poolView, shortModel } from '../lib/pools'
import { Icon } from './Icon'
import { Swirl } from './Swirl'

const NAV: { name: Route['name']; label: string; icon?: string; hint?: string }[] = [
  { name: 'explore', label: 'Explore', icon: 'compass' },
  { name: 'ask', label: 'Ask', hint: '⌥ Space' },
  { name: 'api', label: 'API', icon: 'braces' },
  { name: 'profile', label: 'Profile', icon: 'user' },
]

export function Sidebar({ s }: { s: BridgeState }) {
  const [creating, setCreating] = useState(false)
  const all = [...(s.catalog?.pools ?? []), ...s.local.pools]
  const pools = all.filter((p) => s.local.joined.includes(p.id))
  const lentGb = s.lend?.gb ?? 0
  const credits = creditsOf(s.usage, s.config)
  const lendingPool = s.local.lendingTo
    ? all.find((p) => p.id === s.local.lendingTo)?.name
    : s.config?.pool_name
  const lending = !!s.lend?.lending

  return (
    <aside className="flex w-[244px] shrink-0 flex-col bg-sidebar">
      <div data-tauri-drag-region className="h-[52px] shrink-0" />
      <div className="px-6 pb-5 text-[30px] font-bold leading-none tracking-[-0.03em]">
        dots<span className="text-signal">.</span>
      </div>

      <nav className="space-y-0.5 px-3">
        {NAV.map((n) => {
          const active = s.route.name === n.name
          return (
            <button
              key={n.name}
              onClick={() => store.nav({ name: n.name } as Route)}
              className={`flex h-10 w-full items-center gap-3 rounded-lg px-3 text-[14.5px] transition-colors ${
                active ? 'bg-active text-ink' : 'text-[#cfcfcf] hover:bg-active/50'
              }`}
            >
              {n.icon ? (
                <Icon name={n.icon} size={19} className="text-dim" />
              ) : (
                <span className="grid size-[19px] place-items-center">
                  <span className="size-2 rounded-full bg-signal" />
                </span>
              )}
              <span className="flex-1 text-left">{n.label}</span>
              {n.hint && <span className="text-[13px] text-faint">{n.hint}</span>}
            </button>
          )
        })}
      </nav>

      <p className="label px-6 pb-2 pt-7 !text-[11px]">Your pools</p>
      <div className="min-h-0 flex-1 space-y-0.5 overflow-y-auto px-3">
        {pools.map((raw) => {
          const p = poolView(raw, s.local.lendingTo === raw.id, lentGb)
          const active = s.route.name === 'pool' && s.route.id === p.id
          return (
            <button
              key={p.id}
              onClick={() => store.nav({ name: 'pool', id: p.id })}
              className={`flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left transition-colors ${
                active ? 'bg-active' : 'hover:bg-active/50'
              }`}
            >
              <Swirl seed={p.id} size={26} cells={16} />
              <span className="min-w-0">
                <span className="block truncate text-[14px] font-medium leading-tight">{p.name}</span>
                <span className="block truncate text-[12px] leading-tight text-dim">
                  {p.macs} {p.macs === 1 ? 'Mac' : 'Macs'} · {shortModel(p.model)}
                </span>
              </span>
            </button>
          )
        })}
        {creating ? (
          <NewPool models={s.catalog?.models ?? []} onDone={() => setCreating(false)} />
        ) : (
          <button
            onClick={() => setCreating(true)}
            className="flex h-10 w-full items-center gap-3 rounded-lg px-3 text-[14px] text-[#cfcfcf] hover:bg-active/50"
          >
            <Icon name="plus" size={18} className="text-dim" /> Start a pool
          </button>
        )}
      </div>

      <div className="m-3 rounded-2xl border border-line bg-card px-3.5 py-3">
        <div className="flex items-start gap-2.5">
          {lending ? (
            <span className="mt-[7px] size-2 rounded-full bg-signal" />
          ) : (
            <span className="mt-[7px] size-2 rounded-full border border-dim" />
          )}
          <div className="min-w-0">
            <p className="text-[14px] font-semibold leading-tight">
              {lending ? `Lending ${lentGb} GB` : 'Not lending'}
            </p>
            <p className="truncate text-[12px] text-dim">
              {s.lend?.device.name ?? 'This Mac'}
              {lending && lendingPool ? ` · ${lendingPool}` : ''}
            </p>
          </div>
        </div>
        <div className="mt-2.5 flex items-start gap-2.5">
          <Icon name="credits" size={16} className="mt-0.5" />
          <div>
            <p className="text-[14px] font-semibold leading-tight">
              {credits.balance === null ? '—' : fmt(credits.balance)} credits
            </p>
            <p className="text-[12px] text-dim">
              {credits.perHour !== null && credits.perHour >= 0.5
                ? `+${fmt(credits.perHour)} an hour`
                : 'earns while you lend'}
            </p>
          </div>
        </div>
      </div>
    </aside>
  )
}

function NewPool({ models, onDone }: { models: string[]; onDone: () => void }) {
  const [name, setName] = useState('')
  const [model, setModel] = useState(models[0] ?? '')
  function create() {
    if (!name.trim()) return
    store.createPool(name.trim(), model)
    onDone()
  }
  return (
    <div className="space-y-2 rounded-lg bg-active/60 p-2.5">
      <input
        autoFocus
        value={name}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') create()
          if (e.key === 'Escape') onDone()
        }}
        placeholder="Pool name"
        className="w-full rounded-md bg-input px-2.5 py-1.5 text-[13px] outline-none placeholder:text-faint"
      />
      <select
        value={model}
        onChange={(e) => setModel(e.target.value)}
        className="w-full rounded-md bg-input px-2 py-1.5 text-[13px] outline-none"
      >
        {models.map((m) => (
          <option key={m}>{m}</option>
        ))}
      </select>
      <div className="flex gap-2">
        <button onClick={create} className="flex-1 rounded-md bg-signal py-1.5 text-[13px] font-semibold text-[#1a0a03]">
          Create
        </button>
        <button onClick={onDone} className="flex-1 rounded-md bg-btn py-1.5 text-[13px]">
          Cancel
        </button>
      </div>
    </div>
  )
}
