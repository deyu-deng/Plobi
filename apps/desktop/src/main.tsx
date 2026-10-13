import './styles.css'
// Side-effect: applies the persisted window translucency on load.
// Loaded unconditionally (not inside the dynamic import below) because the
// pet-overlay window shares this stylesheet — it has no app mount of its own.
import './store/translucency'

import { runBootstrap } from './lib/boot-recovery'

// R-083 — the application now mounts through a dynamic import so that ANY break
// in the module graph (a half-applied HMR shard mid-session is the common one)
// lands on the retry card in ./lib/boot-recovery.ts instead of a white window.
// The card is dependency-free and renders into #root, which this entry point
// otherwise leaves empty.
//
// The pet overlay rides this same bundle (`?win=overlay`) but mounts a tiny,
// transparent, gateway-less surface instead of the full app. It keeps its own
// dynamic import and its own catch — the overlay branch must never be routed
// through the app mount, and the app branch never through the overlay.
runBootstrap({
  isOverlayWindow: new URLSearchParams(window.location.search).get('win') === 'overlay',
  loadApp: () =>
    import('./bootstrap-app').then(({ mountApp }) => {
      mountApp()
    }),
  loadOverlay: () =>
    import('./app/pet-overlay/overlay-root').then(({ mountPetOverlay }) => {
      mountPetOverlay()
    })
})
