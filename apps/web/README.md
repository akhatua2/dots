# web

The dots web app — chat with the pool, watch the worker map, and contribute
compute, all in the browser. Vite + React + TypeScript + Tailwind, deployed to
Vercel.

It talks to a hub's OpenAI-compatible HTTP API (see
[`core/src/dots/app/api.py`](../../core/src/dots/app/api.py)). The engine itself
lives in [`../../core/`](../../core/).

## Invite-only

No hub URL or token is baked into the build. Access comes from an invite link
whose fragment carries the credentials:

```
https://<app>/#t=<DOTS_JOIN_TOKEN>&api=<hub-url>
```

On first open these are saved to `localStorage`. A bare visit (no fragment,
nothing saved) shows "open your invite link".

## Develop

```bash
npm install
npm run dev      # local dev server with HMR
npm run build    # tsc -b && vite build → dist/
npm run lint
```

## Deploy

Pushes to `main` that touch `apps/web/**` trigger
[`.github/workflows/deploy-web.yml`](../../.github/workflows/deploy-web.yml),
which builds and deploys to Vercel. For one-time Vercel linking, see
[`../../docs/development.md`](../../docs/development.md).
