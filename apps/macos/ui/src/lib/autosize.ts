// Ref callback: keep a floating panel's native window exactly as tall as its
// content. Runs from a ref (no effects); the observer is torn down on unmount.
import { getCurrentWindow, LogicalSize } from '@tauri-apps/api/window'

export const autosize = (el: HTMLElement | null) => {
  if (!el) return
  let win: ReturnType<typeof getCurrentWindow>
  try {
    win = getCurrentWindow()
  } catch {
    return // plain browser preview: nothing to resize
  }
  const fit = () => {
    const r = el.getBoundingClientRect()
    void win.setSize(new LogicalSize(Math.ceil(r.width), Math.ceil(r.height)))
  }
  const ro = new ResizeObserver(fit)
  ro.observe(el)
  fit()
  return () => ro.disconnect()
}
