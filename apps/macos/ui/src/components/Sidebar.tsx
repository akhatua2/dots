import { useState } from 'react'
import { store, type BridgeState, type Route } from '../lib/store'
import { isLiveIn, joinedPools } from '../lib/selectors'
import { Icon } from './Icon'
import { PoolMark } from './Fingerprint'
import { LiveCard } from './sidebar/LiveCard'
import { NewPoolForm } from './sidebar/NewPoolForm'

interface NavEntry {
  label: string
  icon?: string
  hint?: string
  route: Route
}

const NAV: NavEntry[] = [
  { label: 'Explore', icon: 'compass', route: { name: 'explore' } },
  { label: 'Ask', hint: '⌥ Space', route: { name: 'ask' } },
  { label: 'API', icon: 'braces', route: { name: 'api' } },
  { label: 'Profile', icon: 'user', route: { name: 'profile' } },
]

const rowBase = 'flex h-[34px] w-full items-center gap-3 rounded-[9px] px-3 text-[15px] transition-colors'

export function Sidebar({ s }: { s: BridgeState }) {
  const [creating, setCreating] = useState(false)
  const pools = joinedPools(s)

  return (
    <aside className="sidebar flex w-[250px] shrink-0 flex-col">
      <div data-tauri-drag-region className="h-[38px] shrink-0" />
      <div className="px-5 pb-[18px] pt-1 text-[26px] font-bold leading-none tracking-[-0.03em]">
        dots<span className="text-signal">.</span>
      </div>

      <nav className="space-y-0.5 px-2.5">
        {NAV.map((entry) => (
          <NavRow key={entry.label} entry={entry} s={s} />
        ))}
      </nav>

      <p className="px-5 pb-2 pt-7 text-[12.5px] text-dim">Your pools</p>
      <div className="min-h-0 flex-1 space-y-0.5 overflow-y-auto px-2.5">
        {pools.map((pool) => {
          const active = s.route.name === 'pool' && s.route.id === pool.id
          return (
            <button
              key={pool.id}
              onClick={() => store.nav({ name: 'pool', id: pool.id })}
              className={`${rowBase} !h-auto !px-2.5 py-[7px] text-left ${active ? 'bg-active' : 'hover:bg-active/50'}`}
            >
              <PoolMark seed={pool.id} size={30} />
              <span className="flex-1 truncate text-[15px] font-medium">{pool.name}</span>
              {isLiveIn(s, pool.id) && <span className="size-[7px] rounded-full bg-signal" />}
            </button>
          )
        })}
        {creating ? (
          <NewPoolForm models={s.catalog?.models ?? []} onDone={() => setCreating(false)} />
        ) : (
          <button onClick={() => setCreating(true)} className={`${rowBase} text-[#cfcfcf] hover:bg-active/50`}>
            <Icon name="plus" size={16} className="text-dim" /> Start a Pool
          </button>
        )}
      </div>

      <button
        onClick={() => store.nav({ name: 'settings' })}
        className={`${rowBase} mx-2.5 !w-auto ${s.route.name === 'settings' ? 'bg-active' : 'text-[#cfcfcf] hover:bg-active/50'}`}
      >
        <Icon name="gear" size={16} className="text-dim" />
        <span className="flex-1 text-left">Settings</span>
        <span className="text-[13px] text-faint">⌘,</span>
      </button>
      <LiveCard s={s} />
    </aside>
  )
}

function NavRow({ entry, s }: { entry: NavEntry; s: BridgeState }) {
  const { label, icon, hint, route } = entry
  const active = s.route.name === route.name
  // The orange dot beside Profile means there is a recap you have not opened.
  const hasNews = label === 'Profile' && !s.local.recapSeen && !!s.catalog?.recap

  return (
    <button
      onClick={() => store.nav(route)}
      className={`${rowBase} ${active ? 'bg-active text-ink' : 'text-[#cfcfcf] hover:bg-active/50'}`}
    >
      {icon ? (
        <Icon name={icon} size={16} className="text-dim" />
      ) : (
        <span className="grid size-4 place-items-center">
          <span className="size-[7px] rounded-full bg-signal" />
        </span>
      )}
      <span className="flex-1 text-left">{label}</span>
      {hasNews && <span className="size-[7px] rounded-full bg-signal" />}
      {hint && <span className="text-[13px] text-faint">{hint}</span>}
    </button>
  )
}
