"""Experimental Factory Droid CLI in-place coder adapter."""

import json
import os
from typing import Any, Optional

from snodo.coders.subprocess_adapter import SubprocessCoderAdapter


class DroidCLIAdapter(SubprocessCoderAdapter):
    """Run ``droid exec`` in the task worktree using the operator's sign-in."""

    coder_name = "droid-cli"
    binary = "droid"
    model_prefix = "droid-cli/"
    install_hint = (
        "Install the Factory Droid CLI (curl -fsSL https://app.factory.ai/cli | sh), "
        "then sign in by running `droid`, or set FACTORY_API_KEY."
    )
    # Deny tools that act outside the worktree or this process tree by default.
    tool_allowlist = (
        "Read", "LS", "Glob", "Grep", "Create", "Edit", "ApplyPatch", "Execute", "TodoWrite",
    )

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.last_usage: dict[str, Optional[int | float | str]] = self._empty_usage()

    @staticmethod
    def _empty_usage() -> dict[str, Optional[int | float | str]]:
        return {
            "input_tokens": None, "output_tokens": None,
            "reasoning_tokens": None, "cache_read_tokens": None,
            "cache_write_tokens": None, "cost": None, "served_model": None,
        }

    def _build_argv(self, prompt: str, project_root: str, model: str) -> list[str]:
        argv = [
            "droid", "exec", "--output-format", "stream-json", "--auto", "medium",
            "--only-tools", ",".join(self.tool_allowlist), "--disable-builtin-skills",
            "--cwd", os.path.abspath(project_root),
        ]
        if model:
            argv.extend(["--model", model])
        argv.append(prompt)
        return argv

    def _begin_subprocess_run(self) -> None:
        self.last_usage = self._empty_usage()

    def _process_output_line(self, line: str) -> str:
        raw = line.rstrip("\n")
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            return raw
        if not isinstance(event, dict):
            return raw
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            model = event.get("model")
            if isinstance(model, str) and model:
                self.last_usage["served_model"] = model
            return ""
        if kind == "completion":
            usage = event.get("usage")
            if isinstance(usage, dict):
                mapping = {
                    "input_tokens": "input_tokens",
                    "output_tokens": "output_tokens",
                    "cache_read_input_tokens": "cache_read_tokens",
                    "cache_creation_input_tokens": "cache_write_tokens",
                    "thinking_tokens": "reasoning_tokens",
                }
                for source, target in mapping.items():
                    value = usage.get(source)
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        self.last_usage[target] = value
            return ""
        if kind == "message":
            return str(event.get("text", "")) if event.get("role") == "assistant" else ""
        if kind == "reasoning":
            return ""
        if kind == "tool_call":
            parameters = event.get("parameters")
            parameters = parameters if isinstance(parameters, dict) else {}
            summary = parameters.get("summary") or parameters.get("command") or ""
            return f"[{event.get('toolName', '')}] {summary}".rstrip()
        if kind == "tool_result":
            error = event.get("error")
            if event.get("isError") and isinstance(error, dict):
                return f"[tool error] {error.get('message', '')}".rstrip()
            return ""
        if kind == "error":
            return f"[error] {event.get('message', '')}".rstrip()
        return str(event.get("text") or event.get("message") or "")

    def _format_output_tail_stdout(self, stdout: str) -> str:
        rendered = [self._process_output_line(line) for line in stdout.splitlines()]
        return "\n".join(line for line in rendered if line)
