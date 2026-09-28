# AI RP Engine — Developer / Agent Guide

This file contains editing rules. Read the root [README](README.md) for startup,
[Platform README](server/platform/README.md) before changing platform behavior,
and [GameServer README](server/gameserver/README.md) before changing a game runtime.
Keep the relevant document in sync with behavior changes.

## Boundaries

- `server/platform/` owns accounts, `platform.db`, catalog payload operations,
  Lobby/API routing, RoomManager, Admin and the legacy web adapter.
- `server/gameserver/` owns one game's protocol, sessions, roles, rounds,
  `game.db`, story, recovery and LLM pipeline. It must not query `platform.db`,
  know RoomManager or room codes, or add `room_id` to its SQL tables.
- `server/main.py` is the platform and maintenance CLI. Normal startup must work
  with no owner, catalog entries, Game or LLM configuration.
- Platform metadata remains in `platform.db`; Template and Game payloads remain
  on disk. Template → Game is a filesystem snapshot, never a live reference.
- Web is the user entry point. `client/admin.py` is loopback only platform
  management, not a player or Host client. Keep the explicit legacy `--game`
  compatibility path isolated from normal Platform startup.

## Runtime invariants

- `world_state` is canonical. WorldUpdater produces a new world, public data,
  views and statusbars; Narrator sees only public data and its role's view,
  statusbar, character sheet and bounded history. Room Chat never enters AI
  context or persisted story history.
- Runtime role IDs are derived `P1..PN` from payload `metadata.json`; never add
  fixed Player A/B logic. Current deployment accepts 2–4 roles, while the core
  supports dynamic N-role.
- `metadata.json` holds `count`, `names`, `title`, `introduction`, `tags`. Only
  `server/gameserver/roles.py` loads or writes it. `roles.json` is solely an
  input to the idempotent legacy migration; do not create new payloads with it.
- A user is identified by `user_id`, never username. Host capability is checked
  server-side against `owner_user_id`, independently of role assignment.
- Maintain one linear timeline. Retry fully reruns a round with preserved
  actions; rollback physically removes later rounds. Do not add branches,
  revisions, soft deletion or partial recovery checkpoints.
- Keep Room capacity, disconnect grace, kick, close and `user_room` membership
  in Platform. GameServer gets callbacks and owner capability, not platform IDs.
- RoomManager is authoritative for same-room reconnect and explicit leave;
  Platform Web uses the leave API, while the legacy single-game path may use
  the game `leave` command. Any AI pipeline failure keeps actions locked until
  the owner runs a full retry.
- Use the shared Starlette adapter in `server/platform/websocket_adapter.py` for
  both web shells. When changing the factory or adapter, run the live smoke
  flow (register → import → create Room → join → close) as well as tests.
- Prefer direct API calls, simple functions and SQLite. Do not add an ORM,
  migration framework, event sourcing or agent framework.

## Repository content

- Commit only `templates/default/` under `templates/`. `data/`, `games/`,
  other templates, SQLite sidecars, credentials, logs and local `.env` files
  are runtime/user data. Never delete local data merely to clean Git; untrack
  it from the index if necessary.
- Keep synthetic tests and fixtures in `tests/`. `web/dist/` is a tracked
  production build; rebuild it after Web changes.
- User-facing Chinese calls a Template “剧本”. Keep internal identifiers
  (`Template`, `template_id`, `templates`, `/api/templates`) unchanged.
- Map protocol error codes to Chinese in the Web presentation layer; keep
  protocol names and database columns in English.

## Checks

```bash
python -m unittest discover
cd web && npx vitest run && npx tsc -b && npm run build
git diff --check
```
