// Option-Space from another app: the same Ask surface in a floating panel.
import { useSyncExternalStore } from 'react'
import { getCurrentWindow } from '@tauri-apps/api/window'
import { autosize } from './lib/autosize'
import { store } from './lib/store'
import { AskSurface } from './screens/ask/AskSurface'

export function AskPanel() {
  const s = useSyncExternalStore(store.subscribe, store.getSnapshot)
  return (
    <div ref={autosize} className="w-[620px]">
      <AskSurface s={s} hint="close" onClose={() => void getCurrentWindow().hide()} />
    </div>
  )
}
