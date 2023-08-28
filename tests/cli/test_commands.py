import contextlib
import io
import os
import shlex

import pytest
from pytest_regressions.file_regression import FileRegressionFixture

from milatools.cli.commands import main


def _convert_argparse_output_to_pre_py311_format(output: str) -> str:
    """Revert the slight change in the output format of argparse in python 3.11."""
    return output.replace("options:\n", "optional arguments:\n")


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
    """Test that we get a proper output when we use an invalid command (that exits immediately)."""
    monkeypatch.setattr("sys.argv", shlex.split(command))
    buf = io.StringIO()
    with contextlib.suppress(SystemExit), pytest.raises(
        SystemExit
    ), contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        main()
    file_regression.check(_convert_argparse_output_to_pre_py311_format(buf.getvalue()))


# TODO: Perhaps we could use something like this so we can run all tests locally, but skip the ones
# that need to actually connect to the cluster when running on GitHub Actions.
# def dont_run_on_github(*args):
#     return pytest.param(
#         *args,
#         marks=pytest.mark.skipif(
#             "GITHUB_ACTIONS" in os.environ,
#             reason="We don't run this test on GitHub Actions for security reasons.",
#         ),
#     )


@pytest.mark.parametrize(
    "command", ["mila docs conda", "mila intranet", "mila intranet idt"]
)
def test_check_command_output(
    command: str,
    file_regression: FileRegressionFixture,
    monkeypatch: pytest.MonkeyPatch,
):
    """Test that the --help text matches what's expected (and is stable over time)."""

    monkeypatch.setattr("webbrowser.open", lambda url: None)
    monkeypatch.setattr("sys.argv", shlex.split(command))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        main()
    output: str = buf.getvalue()
    file_regression.check(_convert_argparse_output_to_pre_py311_format(output))
