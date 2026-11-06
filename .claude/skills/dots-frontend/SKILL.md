---
name: dots-frontend
description: How to write and review dots frontend code, the SwiftUI macOS app in apps/macos and the React web app in apps/web. Use for any UI work, new screens, components, copy, or styling in this repo.
---

# dots frontend

The design is Sourabh Daroji's. Match it exactly; do not improvise a look.
Mockups and the case study: https://sourabhdaroji.com/work/dots-decentralized-inference

## Design language

- Neutral graphite and one electric orange (`#FF5A1F`). Orange only ever marks you, what is live,
  and the one main action on screen. A second orange button on a screen is a bug.
- The dithered swirl and the contribution fingerprint belong to pool emblems and the moments
  between screens (joining, unlocking). They never decorate the interface.
- System font (SF Pro) for the interface, Helvetica Neue Bold for the big moments. No web fonts in the Mac app.
- No emojis. Icons are SF Symbols in the Mac app and our own SVG set in the web app.
- No stock controls left at their defaults: pickers, toggles, menus, text fields, scroll bars,
  tooltips, focus rings. Style every control into the design or build one. If a control looks like it
  came from the OS, it is unfinished.
- No bento grids of identical cards. Use hairlines, lists and one level of cards, and let
  the content decide the structure.
- Flows (join, invite, boost, create a key), Settings and Ask all live inside the main window as
  screens or inline forms. Nothing opens a separate window, popup, sheet or floating panel. The one
  floating surface is the menu bar dropdown, because the OS owns it. ⌥ Space brings the window
  forward on the Ask screen.

## Copy

- Sentence case. Plain verbs. Active voice. A button says what happens ("Join and Lend 16 GB").
- One name per action everywhere. Errors say what went wrong and what to do, and never apologize.
- No all-caps eyebrow above every heading. `CapLabel` is for real section labels only.

## Code

- No hardcoded values in views: no literal colors, sizes, strings that are data, or sample content.
  Colors and type come from `Design/Theme.swift` (`D`). Content comes from the model or the backend.
  Sample data lives only in `Model/SampleData.swift` and the mock cloud, and says so.
- Views are declarative and small. One view per file once it passes ~150 lines. Business logic
  lives in `Service/` and `Model/AppModel.swift`, never in a view body.
- Reuse `Design/Components.swift` (`card()`, `Hairline`, `CapLabel`, ...) before writing a new modifier.
  If you build a second copy of something, extract it.
- Names say what a thing is. Comments explain why, not what. No commented-out code.
- Web app (`apps/web`): React with Tailwind. **No `useEffect`.** Read external state with
  `useSyncExternalStore` and keep side effects in stores outside React. Use ref callbacks for DOM
  work. Settings come from config, not constants in components. Cache paid APIs (Mapbox) and
  create the map once. (Debt: six `useEffect` calls remain in `apps/web/src` to migrate.)

## Before you call it done

1. It builds with no warnings you introduced.
2. Run it and look at it next to the mockup. A passing compile says nothing about how it looks.
3. Mac app: `dots --selftest` passes and `dots --snapshot ~/Desktop/dots-shots` shows the screen you changed.
4. Web app: `npm run lint` and `npm run build` pass.
5. Every state is real: empty, loading, error, and the long-text case.
