import { useCallback, useRef } from 'react'
import createGlobe from 'cobe'

export interface Marker {
  location: [number, number]
  size: number
}

// A dotted globe (cobe, ~5KB WebGL) tuned to our light/paper palette: warm-gray
// land dots, sienna worker markers. Auto-rotates. Rebuilds only when the marker
// set changes, preserving rotation across rebuilds via a ref.
export function Globe({ markers }: { markers: Marker[] }) {
  const phi = useRef(0)
  const key = markers.map((m) => `${m.location[0]},${m.location[1]}:${m.size}`).join('|')

  // React calls this with the canvas once it is in the page, and runs what it returns when the canvas
  // goes or the markers change (a new callback), so the globe is built and torn down with no effect.
  const mount = useCallback((canvas: HTMLCanvasElement | null) => {
    if (!canvas) return
    let width = canvas.offsetWidth
    const onResize = () => (width = canvas.offsetWidth)
    window.addEventListener('resize', onResize)

    const globe = createGlobe(canvas, {
      devicePixelRatio: Math.min(window.devicePixelRatio, 2),
      width: width * 2,
      height: width * 2,
      phi: phi.current,
      theta: 0.25,
      dark: 0,
      diffuse: 1.1,
      mapSamples: 16000,
      mapBrightness: 5.2,
      baseColor: [0.87, 0.85, 0.8],
      markerColor: [0.78, 0.34, 0.12],
      glowColor: [0.97, 0.95, 0.92],
      markers,
    })

    let raf = 0
    const tick = () => {
      phi.current += 0.004
      globe.update({ phi: phi.current, width: width * 2, height: width * 2 })
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)

    return () => {
      cancelAnimationFrame(raf)
      globe.destroy()
      window.removeEventListener('resize', onResize)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  return (
    <canvas
      ref={mount}
      className="aspect-square w-full max-w-[440px]"
      style={{ contain: 'layout paint size' }}
    />
  )
}
