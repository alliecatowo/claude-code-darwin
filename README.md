# darwin

A self-improving coding-agent harness — MiMo-Code's feature set (reference spec:
[docs/SPEC.md](docs/SPEC.md)) re-implemented as a plugin, first for **opencode** v1, then for
**Claude Code**. Not a fork: 100% feature parity via plugin APIs where possible, with documented
exceptions ([docs/FEASIBILITY.md](docs/FEASIBILITY.md)).

> **Status: work in progress.** The opencode plugin exists; the Claude Code port ships skills only so far,
> and nothing is published to npm. MIT licensed.

- [docs/RUNDOWN.md](docs/RUNDOWN.md) — every capability × limits × keep/toss verdict (decision doc)
- [docs/SPEC.md](docs/SPEC.md) — complete MiMo-Code specification (vendored @ `092e42f`, 2026-08-29)
- [docs/FEASIBILITY.md](docs/FEASIBILITY.md) — plugin-parity mapping + concerns
- [docs/PLAN.md](docs/PLAN.md) — porting plan (opencode first, then Claude Code)

## Install (Claude Code, skills only)

The bundled skills are installable as a Claude Code plugin. This is skills only: the
host-agnostic harness (memory, scheduler, economics) is not ported to Claude Code yet.

```
/plugin marketplace add alliecatowo/claude-code-darwin
/plugin install darwin@darwin
```

## Status

Pre-release and unpublished (no npm package or release yet). Port status:

| Host | Package | State |
| --- | --- | --- |
| opencode v1 | `packages/opencode` | In progress; shared logic lives in `packages/core` |
| Claude Code | `packages/claude` | Not started (phase 2, see [docs/PLAN.md](docs/PLAN.md)); no Claude Code plugin exists in this repo yet |

`packages/core` is host-agnostic and has tests (`bun test packages/core`).

## Development

```
npm ci                     # install (npm workspaces)
npm run typecheck          # tsc over packages/core and packages/opencode
bun test packages/core     # unit tests (store, memory, scheduler, goal, economics); Bun 1.4+
```

`packages/core` uses `bun:sqlite`, so tests need Bun; CI runs both commands on every push and pull
request. Agents working in this repo: see [`.opencode/`](.opencode) and [docs/PLAN.md](docs/PLAN.md).
