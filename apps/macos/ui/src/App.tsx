import { useState, useSyncExternalStore } from 'react'
import { store } from './lib/store'
import { Sidebar } from './components/Sidebar'
import { JoinOverlay } from './components/JoinOverlay'
import { Icon } from './components/Icon'
import { Explore } from './screens/Explore'
import { PoolPage } from './screens/PoolPage'
import { Profile } from './screens/Profile'
import { Api } from './screens/Api'
import { AskView } from './screens/AskView'
import { MapPanel } from './MapPanel'

const HAS_MAP = !!import.meta.env.VITE_MAPBOX_TOKEN

export function App() {
  // All bridge state comes from the external store — no effects in here.
  const s = useSyncExternalStore(store.subscribe, store.getSnapshot)
  const [showMap, setShowMap] = useState(false)
  // Mount the map only after the first open, then keep it alive (visibility
  // toggled) so the Mapbox instance is created once.
  const [mapMounted, setMapMounted] = useState(false)

  function toggleMap() {
    setMapMounted(true)
    setShowMap((v) => {
      if (!v) void store.refreshNodes()
      return !v
    })
  }

  const r = s.route
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
            {r.name === 'explore' && <Explore s={s} />}
            {r.name === 'pool' && <PoolPage s={s} id={r.id} />}
            {r.name === 'profile' && <Profile s={s} />}
            {r.name === 'api' && <Api s={s} />}
            {r.name === 'ask' && <AskView s={s} />}
          </div>
          {HAS_MAP && mapMounted && (
            <aside
              className={`shrink-0 overflow-hidden border-l border-line transition-[width] duration-200 ${
                showMap ? 'w-[420px]' : 'w-0'
              }`}
            >
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
