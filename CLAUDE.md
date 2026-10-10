# prose-forge (ff-editor-agent)

Two halves: the hosted, stateless MCP server (`remote_server.py`, deployed to
Vercel as `prose-forge`) and the Claude plugin in `plugin/` (the
`write-in-my-voice` skill, hooks, and the connector pointing at the hosted
server). `mcp_server.py` and `src/` are the older local pipeline.

## Shipping changes

Never push straight to `main`. Every change goes on a branch and a PR that
Joel merges.

- **Plugin changes** (anything under `plugin/`): bump `version` in
  `plugin/.claude-plugin/plugin.json` in the same PR. The plugin is installed
  from Joel's personal claude.ai settings, which only re-syncs when a PR that
  bumps the plugin version is merged to `main`; direct pushes never update
  it, and Claude Code users only get a new copy when `version` changes.
  Patch for fixes and wording, minor for new behavior. Keep `version` in
  `plugin.json` only, not in `.claude-plugin/marketplace.json`.
- **Server changes** (`remote_server.py`, `*_app.html`, `src/prose_forge/`
  code the server imports): Vercel deploys every push to `main` on its own,
  so merging is the deploy. Check the production deployment reaches READY and
  that `tools/list` on https://prose-forge-tau.vercel.app/mcp shows the
  change.
- A change touching both does both in one PR.

## Checks before opening a PR

    uv run ruff check .
    FORGE_MOCK=1 uv run pytest
    cd tests/ui && npm install && npm test   # picker + triage apps vs the official MCP Apps host

## Gotchas

- The repo root `.mcp.json` registers the local pipeline server as
  `prose-forge`, the same name as the hosted connector, so a session with
  this repo attached sees the local tools instead of the hosted ones.
- `tests/ui/package-lock.json` is generated and ignored; deps are pinned in
  `package.json`.
