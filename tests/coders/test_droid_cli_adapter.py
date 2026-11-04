import json
import os
import sys
import time
from pathlib import Path

import pytest
import psutil
from git import Repo

from snodo.coders import CODER_REGISTRY, check_coder_available, resolve_coder_name
from snodo.coders.base import (
    CoderTimeoutError, CoderUnavailableError, LLMCallError, SnodoMutationError,
)
from snodo.coders.droid_cli_adapter import DroidCLIAdapter
from snodo.coders.subprocess_adapter import SubprocessCoderAdapter
from snodo.core.interfaces import ExecutionError, NoFileOperationsError, Task, TaskSpec
from snodo.tools.git import GitMCP
from snodo.tools.workspace import WorkspaceMCP
from tests.coders.test_adapter_conformance import _ExecutorHarness


FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name):
    return (FIXTURES / f"droid-0.233.0-{name}").read_text()


@pytest.mark.parametrize("model, bare", [
    ("droid-cli/claude-sonnet-5", "claude-sonnet-5"),
    ("droid-cli/custom:example-0", "custom:example-0"),
    ("gpt-4o", ""), ("", ""),
])
def test_routes_and_exact_argv(tmp_path, model, bare):
    adapter = DroidCLIAdapter(model=model, workspace=tmp_path)
    assert CODER_REGISTRY["droid-cli"] is DroidCLIAdapter
    assert resolve_coder_name("droid-cli/claude-sonnet-5") == "droid-cli"
    assert adapter._bare_model() == bare
    expected = [
        "droid", "exec", "--output-format", "stream-json", "--auto", "medium",
        "--only-tools", "Read,LS,Glob,Grep,Create,Edit,ApplyPatch,Execute,TodoWrite",
        "--disable-builtin-skills", "--cwd", str(tmp_path),
    ]
    if bare:
        expected += ["--model", bare]
    assert adapter._build_argv("prompt", str(tmp_path), adapter._bare_model()) == expected + ["prompt"]


def test_cwd_is_absolute_for_a_relative_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    adapter = DroidCLIAdapter(workspace=Path("work"))
    argv = adapter._build_argv("prompt", "work", "")
    assert argv[argv.index("--cwd") + 1] == os.path.join(os.getcwd(), "work")


def test_prompt_warns_about_protocol_and_protected_paths(tmp_path):
    adapter = DroidCLIAdapter(workspace=tmp_path)
    adapter.protected_paths = ["README.md", "secrets/**"]
    spec = TaskSpec(description="do work", constraints=[])
    prompt = adapter._build_prompt(spec)
    assert all(path in prompt for path in ("README.md", "secrets/**", ".snodo/"))
    assert "not a sandbox" in prompt
    adapter.protected_paths = []
    prompt = adapter._build_prompt(spec)
    assert "README.md" not in prompt
    assert "secrets/**" not in prompt
    assert ".snodo/" in prompt


@pytest.mark.parametrize("event, expected", [
    ({"type": "system", "subtype": "init", "model": "model-placeholder"}, ""),
    ({"type": "system", "subtype": "notice", "message": "model clamped"}, "model clamped"),
    ({"type": "message", "role": "assistant", "text": "OK"}, "OK"),
    ({"type": "message", "role": "user", "text": "private prompt"}, ""),
    ({"type": "reasoning", "text": "private reasoning"}, ""),
    ({"type": "tool_call", "toolName": "Execute",
      "parameters": {"summary": "Run tests", "command": "pytest"}}, "[Execute] Run tests"),
    ({"type": "tool_call", "toolName": "Execute",
      "parameters": {"command": "pytest"}}, "[Execute] pytest"),
    ({"type": "tool_result", "isError": True, "error": {"message": "blocked"}},
     "[tool error] blocked"),
    ({"type": "tool_result", "isError": False, "text": "hidden"}, ""),
    ({"type": "error", "message": "authentication failed"}, "[error] authentication failed"),
    ({"type": "completion", "finalText": "duplicate"}, ""),
    ({"type": "new", "text": "notice"}, "notice"),
    ({"type": "new", "message": "diagnostic"}, "diagnostic"),
    ({"type": "new"}, ""),
])
def test_stream_rendering_table(tmp_path, event, expected):
    adapter = DroidCLIAdapter(workspace=tmp_path)
    line = json.dumps(event)
    assert adapter._process_output_line(line) == expected
    assert adapter._format_output_tail_stdout(line) == expected


def test_usage_is_optional_numeric_and_reset_per_run(tmp_path):
    adapter = DroidCLIAdapter(workspace=tmp_path)
    assert adapter._format_output_tail_stdout(_fixture("stream.jsonl")) == "OK"
    assert adapter.last_usage == {
        "input_tokens": 11855, "output_tokens": 276, "cache_read_tokens": 6400,
        "cache_write_tokens": 0, "reasoning_tokens": 135, "cost": None,
        "served_model": "model-placeholder",
    }
    adapter._begin_subprocess_run()
    for usage in (None, [], "unexpected", {}, {
        "input_tokens": True, "output_tokens": False, "thinking_tokens": "1",
        "cache_read_input_tokens": True, "cache_creation_input_tokens": False,
    }):
        assert adapter._process_output_line(json.dumps({"type": "completion", "usage": usage})) == ""
    assert all(value is None for value in adapter.last_usage.values())
    for line in ("not-json", "[]", "null", "42"):
        assert adapter._process_output_line(line + "\n") == line


def _fake_droid(tmp_path, monkeypatch, body):
    executable_dir = tmp_path / "bin"
    executable_dir.mkdir()
    fake = executable_dir / "droid"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "if sys.argv[1:] == ['--version']:\n"
        "    print('0.233.0')\n"
        "    sys.exit(0)\n" + body
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{executable_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return fake


def test_fake_droid_runs_in_workspace_and_scrubs_context(tmp_path, monkeypatch):
    fake = _fake_droid(tmp_path, monkeypatch,
        "import json, os, sys\n"
        "print(json.dumps({'type': 'message', 'role': 'assistant', 'text': json.dumps({"
        "'cwd': os.getcwd(), 'argv': sys.argv[1:], 'job': os.getenv('SNODO_JOB_ID'), "
        "'root': os.getenv('SNODO_PROJECT_ROOT'), 'coder': os.getenv('SNODO_CODER_SUBPROCESS')})}))\n"
        "print(json.dumps({'type': 'completion', 'usage': {'input_tokens': 2}}))\n"
    )
    monkeypatch.setenv("SNODO_JOB_ID", "j_private")
    monkeypatch.setenv("SNODO_PROJECT_ROOT", str(tmp_path / "real-project"))
    monkeypatch.delenv("SNODO_CODER_SUBPROCESS", raising=False)
    adapter = DroidCLIAdapter(workspace=tmp_path)
    seen = []
    adapter.progress_callback = seen.append
    argv = adapter._build_argv("prompt", str(tmp_path), "")
    proc = adapter._run_subprocess(argv, str(tmp_path))
    assert proc.returncode == 0
    assert json.loads(seen[0]) == {
        "cwd": str(tmp_path), "argv": argv[1:], "job": None, "root": None, "coder": "1",
    }
    assert os.environ["SNODO_JOB_ID"] == "j_private"
    assert os.environ["SNODO_PROJECT_ROOT"] == str(tmp_path / "real-project")
    assert "SNODO_CODER_SUBPROCESS" not in os.environ
    assert adapter.last_usage["input_tokens"] == 2
    assert adapter._resolve_binary_path() == str(fake)


def test_forbidden_flags_never_appear(tmp_path):
    adapter = DroidCLIAdapter(workspace=tmp_path)
    argv = adapter._build_argv("prompt", str(tmp_path), "test-model")
    forbidden = {
        "--skip-permissions-unsafe", "-w", "--worktree", "--mission", "-s",
        "--session-id", "--fork", "--use-spec", "--input-format", "--settings",
    }
    assert not forbidden.intersection(argv)
    assert argv.count("--auto") == 1
    assert argv[argv.index("--auto") + 1] == "medium"
    assert "--disable-builtin-skills" in argv


def test_tool_allowlist_is_exact_and_excludes_outside_actions(tmp_path):
    adapter = DroidCLIAdapter(workspace=tmp_path)
    argv = adapter._build_argv("prompt", str(tmp_path), "")
    expected = ("Read", "LS", "Glob", "Grep", "Create", "Edit", "ApplyPatch", "Execute", "TodoWrite")
    assert adapter.tool_allowlist == expected
    assert argv.count("--only-tools") == 1
    tools = tuple(argv[argv.index("--only-tools") + 1].split(","))
    assert tools == expected
    assert not set(tools).intersection({
        "SlackPostMessage", "CreateAutomation", "RunAutomation", "StartMissionRun",
        "Task", "SpawnAgent", "FetchUrl", "WebSearch", "Skill", "AskUser",
    })


@pytest.mark.parametrize("name", ["blocked-write.jsonl", "auth.jsonl"])
def test_error_event_survives_with_empty_stderr(tmp_path, name):
    adapter = DroidCLIAdapter(workspace=tmp_path)
    events = [json.loads(line) for line in _fixture(name).splitlines()]
    # Do not let a duplicate assistant message mask a dropped error event.
    events = [event for event in events if event["type"] in {"system", "error"}]
    tail = adapter._combined_output_tail("\n".join(map(json.dumps, events)), "")
    for event in events:
        if event["type"] == "error":
            assert f"[error] {event['message']}" in tail


@pytest.fixture
def git_workspace(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    # Use the existing identity, never change Git configuration.
    repo = Repo.init(root)
    (root / "README.md").write_text("# Initial\n")
    repo.index.add(["README.md"])
    repo.index.commit("initial")
    try:
        yield root
    finally:
        repo.close()


@pytest.mark.parametrize("name, stderr, message", [
    ("auth.jsonl", "", "FACTORY_API_KEY is set but appears to be invalid"),
    ("blocked-write.jsonl", "", "insufficient permission to proceed"),
    ("", "bad-model.txt", "Invalid model: not-a-real-model"),
])
def test_exit_one_without_changes_preserves_diagnostic(
    tmp_path, monkeypatch, git_workspace, name, stderr, message,
):
    stdout = _fixture(name) if name else ""
    error = _fixture(stderr) if stderr else ""
    _fake_droid(tmp_path, monkeypatch,
        f"sys.stdout.write({stdout!r})\nsys.stderr.write({error!r})\nsys.exit(1)\n")
    adapter = DroidCLIAdapter(workspace=git_workspace)
    with pytest.raises(LLMCallError, match=message):
        adapter.implement(TaskSpec(description="do work", constraints=[]))
    assert message in adapter.last_output_tail
    assert adapter.last_report is None
    assert not adapter._read_changes_from_disk()


def test_exit_zero_without_changes_is_refused_by_executor(tmp_path, monkeypatch, git_workspace):
    _fake_droid(tmp_path, monkeypatch, f"print({_fixture('stream.jsonl')!r})\n")
    adapter = DroidCLIAdapter(workspace=git_workspace)
    spec = TaskSpec(description="do work", constraints=[])
    assert adapter.implement(spec).files == []
    assert adapter.last_report is None
    harness = _ExecutorHarness()
    git = GitMCP(str(git_workspace))
    try:
        with pytest.raises(NoFileOperationsError):
            harness._default_executor(
                Task(id="task_empty", spec="do work"), None, adapter,
                WorkspaceMCP(str(git_workspace)), git,
            )
    finally:
        git.repo.close()


def test_protocol_mutation_is_refused_before_commit(tmp_path, monkeypatch, git_workspace):
    _fake_droid(tmp_path, monkeypatch,
        "from pathlib import Path\n"
        "Path('.snodo').mkdir(exist_ok=True)\n"
        "Path('.snodo/protocol.yml').write_text('changed')\n"
        "Path('feature.py').write_text('feature = True\\n')\n")
    with Repo(git_workspace) as repo:
        before = repo.head.commit.hexsha
        adapter = DroidCLIAdapter(workspace=git_workspace)
        with pytest.raises(SnodoMutationError, match="protocol.yml"):
            adapter.implement(TaskSpec(description="do work", constraints=[]))
        assert repo.head.commit.hexsha == before
        assert not repo.index.diff("HEAD")


def test_operator_checkout_write_is_detected(tmp_path, monkeypatch, git_workspace):
    assert issubclass(DroidCLIAdapter, SubprocessCoderAdapter)
    worktree = tmp_path / "isolated"
    with Repo(git_workspace) as repo:
        repo.git.worktree("add", "-b", "task-isolated", str(worktree))
    outside = git_workspace / "README.md"
    _fake_droid(tmp_path, monkeypatch,
        "from pathlib import Path\n"
        f"Path({str(outside)!r}).write_text('escaped')\n")
    harness = _ExecutorHarness()
    harness._project_root = str(git_workspace)
    harness._worktree_path = str(worktree)
    adapter = DroidCLIAdapter(workspace=worktree)
    git = GitMCP(str(worktree))
    try:
        with pytest.raises(ExecutionError, match="Workspace escape detected.*operator checkout changed"):
            harness._default_executor(
                Task(id="task_escape", spec="do work"), None, adapter,
                WorkspaceMCP(str(worktree)), git,
            )
        assert outside.read_text() == "escaped"
    finally:
        git.repo.close()


def test_silence_timeout_kills_fake_process(tmp_path, monkeypatch, git_workspace):
    pid_path = tmp_path / "droid.pid"
    _fake_droid(tmp_path, monkeypatch,
        "import os, time\nfrom pathlib import Path\n"
        f"Path({str(pid_path)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(30)\n")
    adapter = DroidCLIAdapter(
        workspace=git_workspace, silence_timeout_seconds=3, timeout_seconds=30,
    )
    try:
        started = time.monotonic()
        with pytest.raises(CoderTimeoutError) as error:
            adapter.implement(TaskSpec(description="do work", constraints=[]))
        # The wall clock would also kill the fake; only an early halt proves the silence kill.
        assert time.monotonic() - started < 20
        assert error.value.timeout_limit == "silence"
        assert adapter.last_timeout_limit == "silence"
        assert not psutil.pid_exists(int(pid_path.read_text()))
    finally:
        if pid_path.exists() and psutil.pid_exists(int(pid_path.read_text())):
            psutil.Process(int(pid_path.read_text())).kill()


def test_implement_sends_flags_model_cwd_and_prompt_to_droid(tmp_path, monkeypatch, git_workspace):
    record = tmp_path / "launch.json"
    _fake_droid(tmp_path, monkeypatch,
        "import json, os\nfrom pathlib import Path\n"
        f"Path({str(record)!r}).write_text(json.dumps("
        "{'argv': sys.argv[1:], 'cwd': os.getcwd()}))\n"
        "Path('feature.py').write_text('def feature(): pass\\n')\n")
    adapter = DroidCLIAdapter(model="droid-cli/test-model", workspace=git_workspace)
    artifact = adapter.implement(TaskSpec(description="add a feature", constraints=[]))
    launch = json.loads(record.read_text())
    argv = launch["argv"]
    assert argv[:argv.index("--cwd")] == [
        "exec", "--output-format", "stream-json", "--auto", "medium",
        "--only-tools", "Read,LS,Glob,Grep,Create,Edit,ApplyPatch,Execute,TodoWrite",
        "--disable-builtin-skills",
    ]
    assert argv[argv.index("--cwd") + 1] == os.path.abspath(git_workspace)
    assert argv[argv.index("--model") + 1] == "test-model"
    assert "add a feature" in argv[-1]
    assert os.path.realpath(launch["cwd"]) == os.path.realpath(git_workspace)
    assert [f.path for f in artifact.files] == ["feature.py"]


def test_sandboxed_host_coder_is_refused(tmp_path, monkeypatch, git_workspace):
    marker = tmp_path / "spawned"
    _fake_droid(tmp_path, monkeypatch,
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    adapter = DroidCLIAdapter(workspace=git_workspace, sandboxed=True)
    with pytest.raises(CoderUnavailableError, match="cannot honour sandboxed"):
        adapter.implement(TaskSpec(description="do work", constraints=[]))
    assert not marker.exists()


def test_missing_binary_reports_install_hint(monkeypatch, git_workspace):
    monkeypatch.setenv("PATH", "")
    adapter = DroidCLIAdapter(workspace=git_workspace)
    assert check_coder_available("droid-cli") == ("droid", adapter.install_hint)
    with pytest.raises(CoderUnavailableError) as error:
        adapter._implement_in_place(TaskSpec(description="do work", constraints=[]))
    assert adapter.install_hint in str(error.value)
