# Spec: Experimental `droid-cli` coder

## Problem

Snodo can drive Claude Code, Codex, OpenCode and Antigravity as host-CLI
coders, but not Factory's Droid CLI. `droid exec` also differs from those CLIs
in three ways an adapter must handle. Its default tool set comes from the
operator's account and organisation and includes tools that act outside the
worktree, such as automations, missions, subagents, web access and connector
posting, whose effects Snodo cannot see. Its autonomy tiers rest on the
model's own risk label for each command. And in `stream-json` mode, permission
stops and authentication failures arrive as `error` events on stdout with an
empty stderr.

## Fix

Add `DroidCLIAdapter` (`coders/droid_cli_adapter.py`), a
`SubprocessCoderAdapter` registered as `droid-cli` with an explicit
`droid-cli/` branch in `resolve_coder_name()` and a conformance driver. It runs:

```text
droid exec --output-format stream-json --auto medium
  --only-tools Read,LS,Glob,Grep,Create,Edit,ApplyPatch,Execute,TodoWrite
  --disable-builtin-skills --cwd <absolute task worktree> [--model <id>] <prompt>
```

- Only an explicit `droid-cli/<id>` model reaches `--model`.
- It never passes `--auto high`, `--auto low`, `--skip-permissions-unsafe`,
  `--worktree`, `--mission`, session continuation, `--use-spec`,
  `--input-format` or `--settings`.
- The `init` event is hidden but supplies the served model. Assistant
  messages, tool calls (`[<toolName>] <summary or command>`), tool errors and
  every `error` event are rendered, so a stdout-only failure reaches the halt output. The
  `completion` event supplies input, output, cache and reasoning tokens. Cost
  stays empty, because Droid reports Factory credits, not money.
- Completion text and exit code are never treated as a verdict or report.

Subclassing keeps the existing guarantees: the `.snodo/` guard, commit
ownership, the produced-nothing fault, wall-clock and silence timeouts with
process cleanup, protected-path guidance, binary provenance and the
`sandboxed` refusal. There are no engine, dependency, configuration, status,
severity or halt-type changes. The coder is experimental (ADR 062, proposed):
Droid updates itself, so its launch flags are pinned by a recorded
`droid exec --help` fixture from 0.233.0.

## Tests

- registration, prefix routing and namespace stripping; exact argv with and
  without a model; an absolute `--cwd`, also for a relative workspace;
- protected paths and `.snodo/` in the prompt; the rendering table; the usage
  mapping and served model from a sanitized 0.233.0 stream fixture;
- a fake `droid` on `PATH` that records its working directory and
  environment, and one that records the argv it receives through
  `implement()`;
- fail-safe cases: forbidden flags never appear; the allowlist is exact and
  excludes outside-world tools; `error` events survive an empty stderr; exit 1
  without changes raises with the diagnostic; exit 0 without changes is
  refused by the executor; a `.snodo/` write and an operator-checkout write
  are refused; silence halts the run before the wall clock and leaves no
  process; `sandboxed` and a missing binary are refused;
- the shared adapter conformance checks, including `head_not_moved`;
- a help-contract test whose canary rejects a flag Droid does not have;
- the regenerated protocol schema publication.

Live runs stay out of CI. With Droid 0.233.0, a trivial task created and
committed a file in about 20 seconds, and `--list-tools` with the adapter's
flags allowed 7 tools against 38 without the allowlist.

## Verify

`uv run pytest tests/ -q -n auto -m "" && uv run ruff check . && uv run lint-imports`

## Touch

`packages/snodo-engine/src/snodo/coders/droid_cli_adapter.py`,
`packages/snodo-engine/src/snodo/coders/__init__.py`,
`tests/coders/test_droid_cli_adapter.py`,
`tests/coders/test_droid_launch_help_contract.py`,
`tests/coders/fixtures/droid-0.233.0-*`,
`tests/coders/test_adapter_conformance.py`, `schemas/protocol-v1.json`,
`README.md`, `docs/coders.md`, `docs/protocol.md`, `docs/runbook.md`,
`docs/architecture.md`, `docs/index.md`,
`docs/decisions/062-droid-cli-experimental-fixed-tool-surface.md`,
`docs/decisions/README.md`, `CHANGELOG.md`, this spec.
