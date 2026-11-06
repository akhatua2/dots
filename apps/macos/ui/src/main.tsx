import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { getCurrentWindow } from '@tauri-apps/api/window'
import { getCurrentWebview } from '@tauri-apps/api/webview'
import '@fontsource-variable/geist'
import '@fontsource/ibm-plex-mono/400.css'
import './index.css'

// One bundle, three native windows: the app, the menu bar dropdown, and Ask.
// The window label picks the surface; it is set before the store module loads.
let label = 'main'
try {
  label = getCurrentWindow().label
} catch {
  // Plain browser (dev preview): pick the surface from the URL hash.
  label = location.hash.slice(1) || 'main'
}
document.documentElement.dataset.surface = label
document.documentElement.dataset.glass = 'on' // until config says otherwise
// Right-click would show the browser's own menu, which does not belong in the app.
document.addEventListener('contextmenu', (e) => e.preventDefault())
// The main window is designed on a larger canvas; scale it to native density.
if (label === 'main') {
  try {
    void getCurrentWebview().setZoom(0.9)
  } catch {
    /* plain browser */
  }
}

const root = createRoot(document.getElementById('root')!)
void (async () => {
  const { App } = await import('./App')
  const { TrayPanel } = await import('./TrayPanel')
  const { AskPanel } = await import('./AskPanel')
  const View = label === 'tray' ? TrayPanel : label === 'ask' ? AskPanel : App
  root.render(
    <StrictMode>
      <View />
    </StrictMode>,
  )
})()
