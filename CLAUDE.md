# dots

Pool people's Macs to run big models. Monorepo: `core/` (Python engine), `apps/macos` (SwiftUI app),
`apps/web` (React), `infra/` (hub deploy), `docs/`.

Read the matching skill before changing code:

- Frontend (Mac app or web): `.claude/skills/dots-frontend/SKILL.md`
- Backend (core, infra): `.claude/skills/dots-backend/SKILL.md`

Rules that apply everywhere:

- No Claude or AI attribution in commits or PR descriptions: no `Co-Authored-By` lines and no "Generated with" footers.
- Never force-push unless asked.
- Use uv for Python. Never `pip install`.
- No hardcoded values: settings in config, content from the backend.
- Run the thing you changed and look at it. A passing build or test is not enough.
