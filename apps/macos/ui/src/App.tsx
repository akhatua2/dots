import { useState, useSyncExternalStore } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { store, type BridgeState, type Msg } from './lib/store'
import { DotMark } from './components/DotMark'
import { Icon } from './components/Icon'
import { MapPanel } from './MapPanel'
import arrowUp from './assets/devices/arrow-up.svg?raw'
import globe from './assets/devices/globe.svg?raw'

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
      if (!v) void store.refreshNodes() // opening → fetch the latest immediately
      return !v
    })
  }

  return (
    <div className="h-full">
      <div className="flex h-full">
        {/* slim rail: logo + profile, nothing clickable (drags the window) */}
        <div className="drag flex w-14 shrink-0 flex-col items-center justify-between py-4">
          <DotMark size={20} />
          <div className="size-8 rounded-full bg-gradient-to-br from-signal/80 to-signal-dim" />
        </div>

        <div className="w-px shrink-0 bg-line" />

        <div className="flex min-h-0 flex-1 flex-col">
          <TopBar s={s} showMap={showMap} onToggleMap={toggleMap} />
          <div className="flex min-h-0 flex-1">
            <div className="flex min-h-0 flex-1 flex-col">
              {s.phase === 'connecting' && <Connecting />}
              {s.phase === 'error' && <ErrorState error={s.error} />}
              {s.phase === 'ready' && <Chat s={s} />}
            </div>
            {/* Map as a right sidebar. Kept mounted once opened (width toggled)
                so the Mapbox instance is created once; the inner fixed width
                keeps the map sized even while collapsed. */}
            {mapMounted && (
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
        </div>
      </div>
    </div>
  )
}

function TopBar({
  s,
  showMap,
  onToggleMap,
}: {
  s: BridgeState
  showMap: boolean
  onToggleMap: () => void
}) {
  return (
    <header className="drag flex items-center justify-end gap-4 px-5 py-3">
      <div className="no-drag flex items-center gap-4">
        {s.account?.model && (
          <span className="font-mono text-[12px] text-dim">{shortModel(s.account.model)}</span>
        )}
        {s.balance !== null && (
          <span className="font-mono text-[13px] tabular-nums text-ink">
            {s.balance.toFixed(3)} CU
          </span>
        )}
        <span
          className="flex items-center gap-2 rounded-full bg-surface-2 px-3 py-1.5 font-mono text-[12px] text-dim"
          title="This Mac is donating compute"
        >
          <span className="size-1.5 rounded-full bg-live pulse" />
          contributing
        </span>
        <button
          onClick={onToggleMap}
          className={`flex items-center gap-1.5 rounded-full px-3 py-1.5 font-mono text-[12px] transition-colors [&>span>svg]:size-3.5 ${
            showMap ? 'bg-signal/15 text-signal' : 'text-dim hover:bg-surface-2 hover:text-ink'
          }`}
        >
          <Icon svg={globe} /> Map
        </button>
        {s.account && (
          <span className="font-mono text-[12px] text-faint">
            {s.account.alias || s.account.account_id.slice(0, 8)}
          </span>
        )}
      </div>
    </header>
  )
}

function Connecting() {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-5">
      <span className="pulse">
        <DotMark size={30} />
      </span>
      <div className="space-y-2 text-center">
        <p className="font-sans text-[15px] text-ink">Connecting to the pool…</p>
        <p className="label">starting a local worker · contributing compute</p>
      </div>
    </div>
  )
}

function ErrorState({ error }: { error: string | null }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3 px-10 text-center">
      <p className="font-sans text-[15px] text-warn">Couldn’t connect</p>
      {error && <p className="label">{error}</p>}
    </div>
  )
}

function Chat({ s }: { s: BridgeState }) {
  const [input, setInput] = useState('')

  function send() {
    if (!input.trim() || s.busy) return
    store.send(input)
    setInput('')
  }

  const composer = (
    <Composer value={input} onChange={setInput} onSend={send} busy={s.busy} />
  )

  // Empty: a single centered input. Active: transcript + input pinned bottom.
  if (s.msgs.length === 0) {
    return (
      <div className="flex min-h-0 flex-1 flex-col items-center justify-center px-6">
        <div className="w-full max-w-2xl space-y-4">
          <p className="label text-center">ask anything · your mac answers and contributes</p>
          {composer}
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-2xl space-y-6 px-6 py-8">
          {s.msgs.map((m, i) => (
            <Entry key={i} m={m} />
          ))}
          {/* Inline ref runs every render, so the view tracks the latest token. */}
          <div ref={(el) => el?.scrollIntoView({ block: 'end' })} />
        </div>
      </div>
      <div className="px-6 pb-5">
        <div className="mx-auto max-w-2xl">{composer}</div>
      </div>
    </div>
  )
}

function Entry({ m }: { m: Msg }) {
  if (m.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] whitespace-pre-wrap rounded-3xl bg-surface-2 px-4 py-2.5 font-sans text-[15px] leading-relaxed text-ink">
          {m.text}
        </div>
      </div>
    )
  }
  return (
    <div>
      <div className="prose-dots font-sans text-[15px] leading-relaxed text-ink">
        <Markdown remarkPlugins={[remarkGfm]}>{m.text}</Markdown>
        {m.pending && <span className="cursor" />}
      </div>
      {!m.pending && typeof m.tps === 'number' && (
        <p className="label mt-1.5">{m.tps} tok/s</p>
      )}
    </div>
  )
}

function Composer({
  value,
  onChange,
  onSend,
  busy,
}: {
  value: string
  onChange: (v: string) => void
  onSend: () => void
  busy: boolean
}) {
  return (
    <div className="rounded-3xl border border-line-2 bg-bg p-2 transition-shadow focus-within:border-faint">
      <textarea
        value={value}
        onChange={(e) => {
          onChange(e.target.value)
          // Auto-grow without an effect: resize on each input.
          e.target.style.height = '0px'
          e.target.style.height = Math.min(e.target.scrollHeight, 200) + 'px'
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault()
            onSend()
          }
        }}
        rows={1}
        placeholder="Message the pool"
        className="max-h-52 w-full resize-none bg-transparent px-3 py-2 font-sans text-[15px] text-ink outline-none placeholder:text-faint"
      />
      <div className="flex justify-end pt-1 pr-1">
        <button
          onClick={onSend}
          disabled={busy || !value.trim()}
          aria-label="Send"
          className="grid size-8 shrink-0 place-items-center rounded-full bg-signal text-bg transition-opacity hover:opacity-90 disabled:bg-line disabled:text-faint [&>span>svg]:size-4"
        >
          <Icon svg={arrowUp} />
        </button>
      </div>
    </div>
  )
}

function shortModel(id: string): string {
  const tail = id.split('/').pop() || id
  return tail.replace(/-4bit$/i, '').replace(/-Instruct$/i, '')
}
