import { useRef } from 'react'
import mapboxgl from 'mapbox-gl'
import 'mapbox-gl/dist/mapbox-gl.css'
import type { PoolNode } from './lib/bridge'
// Real device icons (Lucide, MIT) fetched into assets/devices, imported as raw
// SVG markup. They stroke in currentColor, so the marker sets the color.
import laptop from './assets/devices/laptop.svg?raw'
import monitor from './assets/devices/monitor.svg?raw'
import hardDrive from './assets/devices/hard-drive.svg?raw'
import smartphone from './assets/devices/smartphone.svg?raw'
import { Icon } from './components/Icon'

mapboxgl.accessToken = import.meta.env.VITE_MAPBOX_TOKEN as string

function deviceIcon(device: string | null): string {
  const d = (device || '').toLowerCase()
  if (d.includes('iphone') || d.includes('ipad')) return smartphone
  if (d.includes('mini')) return hardDrive
  if (d.includes('studio') || d.includes('imac') || d.includes('mac pro') || d.includes('macpro'))
    return monitor
  return laptop // MacBook / default
}

// Paint a pin in place: a teardrop whose head is a conic ring (green = share
// contributed, red = share consumed) around a device icon, with a point below
// that lands on the exact location.
function pct(n: PoolNode) {
  return n.ratio === null ? null : Math.round(Math.max(0, Math.min(1, n.ratio)) * 100)
}

function paint(pin: HTMLElement, n: PoolNode) {
  const head = pin.querySelector('.node-marker') as HTMLElement
  const p = pct(n)
  // Idle (no activity this session) → neutral grey ring, not a fake 50/50.
  head.style.background =
    p === null
      ? 'var(--color-line-2)'
      : `conic-gradient(var(--color-live) 0% ${p}%, var(--color-warn) ${p}% 100%)`
  const inner = head.firstElementChild as HTMLElement
  inner.innerHTML = deviceIcon(n.device)
}

const esc = (s: string) =>
  s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!)

// Minimal hover card. The bar/figure reflect this session's live delta; idle
// shows neutral rather than a misleading split.
function popupHtml(n: PoolNode): string {
  const p = pct(n)
  const loc =
    [n.city, n.country].filter((x): x is string => !!x).map(esc).join(', ') || 'unknown'
  const name = esc(n.alias || n.id.slice(0, 8))
  const device = esc(n.device || 'Mac')
  const bar =
    p === null
      ? `<i style="width:100%;background:var(--color-line-2)"></i>`
      : `<i style="width:${p}%;background:var(--color-live)"></i><i style="width:${100 - p}%;background:var(--color-warn)"></i>`
  const figure =
    p === null
      ? `<span class="node-pop-idle">idle</span>`
      : `<span><b>${p}%</b> contributing</span>`
  return `
    <div class="node-pop">
      <div class="node-pop-top">
        <span class="node-pop-name">${name}</span>
        <span class="node-pop-status ${n.ready ? 'on' : ''}">${n.ready ? 'ready' : 'loading'}</span>
      </div>
      <div class="node-pop-meta label">${device} · ${loc}</div>
      <div class="node-pop-bar">${bar}</div>
      <div class="node-pop-stats">
        ${figure}
        <span class="node-pop-cu">${n.earned_cu.toFixed(3)} earned · ${n.spent_cu.toFixed(3)} used CU</span>
      </div>
    </div>`
}

function newMarkerEl(n: PoolNode): HTMLElement {
  const pin = document.createElement('div')
  pin.className = 'node-pin'
  const head = document.createElement('div')
  head.className = 'node-marker'
  const inner = document.createElement('div')
  inner.className = 'node-marker-inner'
  head.appendChild(inner)
  const tip = document.createElement('div')
  tip.className = 'node-tip'
  pin.append(head, tip)
  paint(pin, n)
  return pin
}

export function MapPanel({
  nodes,
  visible,
  onClose,
}: {
  nodes: PoolNode[]
  visible: boolean
  onClose: () => void
}) {
  const map = useRef<mapboxgl.Map | null>(null)
  const markers = useRef<Map<string, mapboxgl.Marker>>(new Map())
  const popup = useRef<mapboxgl.Popup | null>(null)
  // Latest node data by id, so a marker's hover handler always reads fresh
  // stats (the handler is attached once at creation).
  const latest = useRef<Map<string, PoolNode>>(new Map())
  const openId = useRef<string | null>(null) // node whose popup is open

  // Create the map exactly once (ref callback fires on mount). Never re-created
  // across open/close — keeps Mapbox usage to a single style/tile load.
  const initMap = (el: HTMLDivElement | null) => {
    if (!el || map.current) return
    const m = new mapboxgl.Map({
      container: el,
      style: 'mapbox://styles/mapbox/dark-v11',
      center: [-30, 25],
      zoom: 1.3,
      attributionControl: false,
    })
    map.current = m
    popup.current = new mapboxgl.Popup({
      closeButton: false,
      closeOnClick: false,
      // Force the card above the pin and lift it past the pin's height so it
      // never covers the marker.
      anchor: 'bottom',
      offset: 46,
      className: 'node-popup',
    })
    // Small dots at globe scale; expand to full device pins once zoomed in.
    // Toggling a class on the container cascades to all marker elements.
    const applyZoom = () => el.classList.toggle('map-zoomed', m.getZoom() >= 4)
    m.on('zoom', applyZoom)
    m.on('load', applyZoom)
  }

  // Diff markers against the latest nodes. Runs each render via an inline ref
  // (no effects). DOM-only — adds no Mapbox tile requests.
  const sync = () => {
    const m = map.current
    if (!m || !visible) return
    m.resize() // container size may have changed while hidden
    const seen = new Set<string>()
    for (const n of nodes) {
      if (typeof n.lat !== 'number' || typeof n.lng !== 'number') continue
      seen.add(n.id)
      latest.current.set(n.id, n)
      const existing = markers.current.get(n.id)
      if (existing) {
        paint(existing.getElement(), n)
        existing.setLngLat([n.lng, n.lat])
      } else {
        const el = newMarkerEl(n)
        // Hover → styled popup; reads the latest stats for this id.
        el.addEventListener('mouseenter', () => {
          const node = latest.current.get(n.id)
          if (!node || !map.current || !popup.current) return
          openId.current = n.id
          popup.current
            .setLngLat([node.lng as number, node.lat as number])
            .setHTML(popupHtml(node))
            .addTo(map.current)
        })
        el.addEventListener('mouseleave', () => {
          openId.current = null
          popup.current?.remove()
        })
        const mk = new mapboxgl.Marker({ element: el, anchor: 'bottom' })
          .setLngLat([n.lng, n.lat])
          .addTo(m)
        markers.current.set(n.id, mk)
      }
    }
    for (const [id, mk] of markers.current) {
      if (!seen.has(id)) {
        mk.remove()
        markers.current.delete(id)
        latest.current.delete(id)
      }
    }
    // Live-refresh an open popup so a hovered card ticks up with each poll.
    if (openId.current && popup.current?.isOpen()) {
      const node = latest.current.get(openId.current)
      if (node) popup.current.setHTML(popupHtml(node))
    }
  }

  const located = nodes.filter((n) => typeof n.lat === 'number').length

  return (
    <div className="relative h-full w-full bg-bg">
      <div ref={initMap} className="h-full w-full" />
      {/* inline ref re-runs every render, so markers track the latest nodes */}
      <div ref={() => sync()} className="hidden" />

      <div className="pointer-events-none absolute inset-x-0 top-0 flex items-center justify-between px-5 py-3">
        <span className="label rounded-full bg-bg/80 px-2.5 py-1">
          {located} {located === 1 ? 'node' : 'nodes'} on the map
        </span>
        <button
          onClick={onClose}
          aria-label="Close map"
          className="pointer-events-auto grid size-7 place-items-center rounded-full bg-bg/90 text-dim shadow-sm hover:text-ink"
        >
          <Icon name="x" size={14} />
        </button>
      </div>

      <div className="pointer-events-none absolute bottom-3 right-5 flex items-center gap-3 font-mono text-[11px] text-dim">
        <span className="flex items-center gap-1.5">
          <span className="size-2 rounded-full bg-live" /> contributing
        </span>
        <span className="flex items-center gap-1.5">
          <span className="size-2 rounded-full bg-warn" /> using
        </span>
      </div>
    </div>
  )
}
