import { useState, useSyncExternalStore } from 'react'
import { store, type BridgeState } from './lib/store'
import { Sidebar } from './components/Sidebar'
import { JoinOverlay } from './components/JoinOverlay'
import { Icon } from './components/Icon'
import { MapPanel } from './MapPanel'
import { ApiPage } from './screens/api/ApiPage'
import { AskPage } from './screens/ask/AskPage'
import { BoostPage } from './screens/boost/BoostPage'
import { Explore } from './screens/explore/Explore'
import { InvitePage } from './screens/invite/InvitePage'
import { PoolPage } from './screens/pool/PoolPage'
import { Profile } from './screens/profile/Profile'
import { RecapPage } from './screens/recap/RecapPage'
import { SettingsPage } from './screens/settings/SettingsPage'

const HAS_MAP = !!import.meta.env.VITE_MAPBOX_TOKEN

export function App() {
  const s = useSyncExternalStore(store.subscribe, store.getSnapshot)
  const [showMap, setShowMap] = useState(false)
  // Mount the map only after the first open, then keep it alive so the Mapbox
  // instance (and its paid tile load) is created once.
  const [mapMounted, setMapMounted] = useState(false)

  function toggleMap() {
    setMapMounted(true)
    setShowMap((open) => {
      if (!open) void store.refreshNodes()
      return !open
    })
  }

  return (
    <div className="flex h-full">
      <Sidebar s={s} />
      <main className="relative min-w-0 flex-1 bg-bg">
        <div data-tauri-drag-region className="absolute inset-x-0 top-0 z-10 h-9" />
        {HAS_MAP && (
          <button
            onClick={toggleMap}
            aria-label="Pool map"
            className={`absolute right-4 top-3 z-20 grid size-8 place-items-center rounded-full transition-colors ${
              showMap ? 'bg-signal-soft text-signal-text' : 'text-dim hover:bg-surface-2 hover:text-ink'
            }`}
          >
            <Icon name="globe" size={17} />
          </button>
        )}
        <div className="flex h-full">
          <div className="h-full min-w-0 flex-1 overflow-y-auto">
            <Screen s={s} />
          </div>
          {HAS_MAP && mapMounted && (
            <aside className={`shrink-0 overflow-hidden border-l border-line transition-[width] duration-200 ${showMap ? 'w-[420px]' : 'w-0'}`}>
              <div className="h-full w-[420px]">
                <MapPanel nodes={s.nodes} visible={showMap} onClose={() => setShowMap(false)} />
              </div>
            </aside>
          )}
        </div>
      </main>
      {s.join && <JoinOverlay j={s.join} device={s.lend?.device.name ?? 'Mac'} />}
    </div>
  )
}

/** The screen for the current route. */
function Screen({ s }: { s: BridgeState }) {
  const route = s.route
  switch (route.name) {
    case 'explore':
      return <Explore s={s} />
    case 'pool':
      return <PoolPage s={s} id={route.id} />
    case 'invite':
      return <InvitePage s={s} id={route.id} />
    case 'boost':
      return <BoostPage s={s} id={route.id} />
    case 'recap':
      return <RecapPage s={s} />
    case 'profile':
      return <Profile s={s} />
    case 'api':
      return <ApiPage s={s} />
    case 'ask':
      return <AskPage s={s} />
    case 'settings':
      return <SettingsPage s={s} />
  }
}
