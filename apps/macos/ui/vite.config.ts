import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// The UI is bundled into the Tauri app (loaded via tauri://, not a server).
// It talks to the Python bridge only through Tauri commands/events.
export default defineConfig({
  plugins: [react(), tailwindcss()],
})
