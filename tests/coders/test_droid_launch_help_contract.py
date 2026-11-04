"""Keep Droid launch flags aligned with the recorded 0.233.0 help contract."""

import re
from pathlib import Path

import pytest

from snodo.coders.droid_cli_adapter import DroidCLIAdapter


FIXTURE = Path(__file__).parent / "fixtures" / "droid-0.233.0-exec-help.txt"


def _assert_supported_flags(argv: list[str]) -> None:
    flags = set(re.findall(r"(?<!\w)--[a-z][a-z0-9-]*|(?<!\w)-[a-zA-Z]", FIXTURE.read_text()))
    unsupported = {token for token in argv[2:] if token.startswith("-")} - flags
    assert not unsupported, (
        f"{FIXTURE.name} does not list launch flag(s) {sorted(unsupported)}; "
        f"update/verify the recorded help fixture: {FIXTURE}"
    )


@pytest.mark.parametrize("model", ["", "custom:example-0"])
def test_launch_flags_are_listed_in_recorded_exec_help(tmp_path, model):
    adapter = DroidCLIAdapter(workspace=tmp_path)
    _assert_supported_flags(adapter._build_argv("contract prompt", str(tmp_path), model))


def test_help_contract_rejects_unsupported_flag(tmp_path):
    adapter = DroidCLIAdapter(workspace=tmp_path)
    argv = adapter._build_argv("contract prompt", str(tmp_path), "")
    argv.insert(2, "--dangerously-skip-permissions")
    with pytest.raises(AssertionError, match="does not list.*--dangerously-skip-permissions"):
        _assert_supported_flags(argv)
