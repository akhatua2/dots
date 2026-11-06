# apps

End-user clients that talk to a dots hub. The engine lives in [`../core/`](../core/);
these are the things people actually open.

| App | Status | Notes |
|-----|--------|-------|
| [`web/`](web/) | live | Vite + React — chat, pool map, contribute panel. Deployed to Vercel. |
| [`macos/`](macos/) | in progress | native SwiftUI app by Sourabh Daroji: menu bar lending, the ⌥ Space ask panel, pools, credits and the local API. Runs against a mock cloud until the real one exists. |

Future: `windows/`, `linux/`.
