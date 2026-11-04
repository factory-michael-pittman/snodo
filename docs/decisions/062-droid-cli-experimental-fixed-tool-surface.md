# ADR 062 — The droid-cli coder is experimental and runs with a fixed tool surface

**Status:** Proposed  
**Date:** 2026-11-04

## Context

Issue #1085 proposes `droid-cli`, a host-CLI coder for Factory's `droid exec`, built like `claude-cli` and `codex-cli`. Droid differs from those CLIs in ways that need a stated position:

- A `droid exec` session's tools come from the operator's account and organisation. With `--auto medium` alone, the account used for testing allowed 38 tools, including automations, missions, subagents, eval tools, connector posting and web search. Their effects happen outside the worktree, where Snodo's diff readback, snapshots and audit trail cannot see them.
- Autonomy tiers are applied from the model's own risk label for each command, so `--auto` limits what the coder attempts but is not a security boundary.
- There is no option like Claude Code's `--setting-sources user` to skip project-level settings, so a repository's `.factory/` configuration, including hooks, applies to a run.
- Droid updates itself, and only enterprise plans can turn that off, so flags and event shapes can change under a pinned adapter.
- Usage is reported in Factory credits, not money.

## Decision

- Ship `droid-cli` as an experimental built-in host-CLI coder: a `SubprocessCoderAdapter` with an explicit `droid-cli/` route and a conformance driver.
- Launch with a fixed `--only-tools` allowlist of read, search, edit, shell and to-do tools, and with `--disable-builtin-skills`. Operators cannot extend the allowlist.
- Run at `--auto medium`, which allows edits, builds and tests but stops at `git push` and remote writes. Never pass `--auto high` or `--skip-permissions-unsafe`.
- Leave project `.factory/` settings and hooks in effect under the trusted-repository model (ADR 014). An operator who wants coder changes to them flagged can add `.factory/` to `protected_paths`, which detects changes after the run (ADR 058).
- Record token usage and the served model, and leave cost empty.

"Experimental" here means that the conformance suite and offline fail-safe tests cover the adapter, and the inherited guarantees hold: the `.snodo/` guard, commit ownership, the produced-nothing fault, and timeouts with process cleanup. But the launch flags are pinned to a recorded `droid exec --help` from 0.233.0, live runs are not in CI, and no shipped template uses the coder. Unlike the opencode designation in ADR 034, usage records are captured.

## Consequences

Snodo, not Droid, remains the enforcement for what reaches the task branch. The allowlist only removes tools whose effects Snodo cannot observe, and it is not an egress control: shell commands can still reach the network. A renamed tool fails before the run starts, because Droid rejects unknown tool identifiers. A changed or removed flag shows up when the help fixture is refreshed or a live run fails.

Promoting the coder to supported is a checklist: a recorded live smoke test and allowlist check for each Droid release the adapter claims, a named owner for fixture refreshes, and adoption in a shipped template.

## Alternatives

- An installable plugin, following ADR 007's provider pattern: viable, but an out-of-tree coder is not covered by the conformance suite and is missing from the published schema enum. Open question on #1085.
- `--auto low` without `Execute`: safer, but the coder could not run tests. Open question on #1085.
- An operator-extensible allowlist: deferred, because a protocol could re-admit tools whose effects Snodo cannot observe.
- Disabling project hooks per run: not possible with Droid 0.233.0.
