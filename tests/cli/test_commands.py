from __future__ import annotations

import contextlib
import io
import shlex
import textwrap

import pytest
from pytest_regressions.file_regression import FileRegressionFixture

from milatools.cli.commands import main
from milatools.cli.common import _parse_lfs_quota_output

from .common import requires_no_s_flag


def _convert_argparse_output_to_pre_py311_format(output: str) -> str:
    """Revert the slight change in the output of argparse in python 3.11."""
    return output.replace("options:\n", "optional arguments:\n")


@requires_no_s_flag
@pytest.mark.parametrize(
    "command",
    ["mila"]
    + [
        f"mila {command}"
        for command in ["docs", "intranet", "init", "forward", "code", "serve"]
    ]
    + [
        f"mila serve {serve_subcommand}"
        for serve_subcommand in (
            "connect",
            "kill",
            "list",
            "lab",
            "notebook",
            "tensorboard",
            "mlflow",
            "aim",
        )
    ],
)
def test_help(
    command: str,
    file_regression: FileRegressionFixture,
    monkeypatch: pytest.MonkeyPatch,
):
    """Test that the --help text matches what's expected (and is stable over time)."""
    monkeypatch.setattr("sys.argv", shlex.split(command + " --help"))
    buf = io.StringIO()
    with contextlib.suppress(SystemExit), contextlib.redirect_stdout(
        buf
    ), contextlib.redirect_stderr(buf):
        with pytest.raises(SystemExit):
            main()
    output: str = buf.getvalue()
    file_regression.check(_convert_argparse_output_to_pre_py311_format(output))


@requires_no_s_flag
@pytest.mark.parametrize(
    "command",
    [
        "mila",  # Error: Missing a subcommand.
        "mila search conda",
        "mila code",  # Error: Missing the required PATH argument.
        "mila serve",  # Error: Missing the subcommand.
        "mila forward",  # Error: Missing the REMOTE argument.
    ],
)
def test_invalid_command_output(
    command: str,
    file_regression: FileRegressionFixture,
    monkeypatch: pytest.MonkeyPatch,
):
    """Test that we get a proper output when we use an invalid command (that exits
    immediately)."""
    monkeypatch.setattr("sys.argv", shlex.split(command))
    buf = io.StringIO()
    with contextlib.suppress(SystemExit), pytest.raises(
        SystemExit
    ), contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        main()
    file_regression.check(_convert_argparse_output_to_pre_py311_format(buf.getvalue()))


@pytest.mark.parametrize(
    "command", ["mila docs conda", "mila intranet", "mila intranet idt"]
)
def test_check_command_output(
    command: str,
    file_regression: FileRegressionFixture,
    monkeypatch: pytest.MonkeyPatch,
):
    """Run simple commands and check that their output matches what's expected."""

    monkeypatch.setattr("webbrowser.open", lambda url: None)
    monkeypatch.setattr("sys.argv", shlex.split(command))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        main()
    output: str = buf.getvalue()
    file_regression.check(_convert_argparse_output_to_pre_py311_format(output))


used_kbytes = 95764232
limit_kbytes = 104857600
used_files = 908504
limit_files = 1048576


def _kb_to_gb(kb: int) -> float:
    return kb / (1024**2)


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        (
            textwrap.dedent(
                f"""\
                Disk quotas for usr normandf (uid 1471600598):
                     Filesystem  kbytes   quota   limit   grace   files   quota   limit   grace
                /home/mila/n/normandf
                                {used_kbytes}       0 {limit_kbytes}       -  {used_files}       0 {limit_files}       -
                uid 1471600598 is using default block quota setting
                uid 1471600598 is using default file quota setting
                """
            ),
            (
                (_kb_to_gb(used_kbytes), _kb_to_gb(limit_kbytes)),
                (used_files, limit_files),
            ),
        ),
        (
            textwrap.dedent(
                f"""\
                Disk quotas for usr normandf (uid 3098083):
                     Filesystem  kbytes   quota   limit   grace   files   quota   limit   grace
                 /home/normandf {used_kbytes}  {limit_kbytes} {limit_kbytes}       -  {used_files}  {limit_files}  {limit_files}       -
                """
            ),
            (
                (_kb_to_gb(used_kbytes), _kb_to_gb(limit_kbytes)),
                (used_files, limit_files),
            ),
        ),
    ],
)
def test_parse_lfs_quota_output(
    output, expected: tuple[tuple[float, float], tuple[int, int]]
):
    assert _parse_lfs_quota_output(output) == expected
