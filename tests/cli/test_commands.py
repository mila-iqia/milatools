import contextlib
import io
import shlex
import sys

import pytest
from pytest_regressions.file_regression import FileRegressionFixture

from milatools.cli.commands import main


@pytest.mark.parametrize(
    "command",
    ["mila"]
    + [
        f"mila {command}"
        for command in ["", "docs", "intranet", "init", "forward", "code", "serve"]
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
    command: str, file_regression: FileRegressionFixture, monkeypatch: pytest.MonkeyPatch
):
    """Test that the --help text matches what's expected (and is stable over time)."""
    monkeypatch.setattr(sys, "argv", shlex.split(command + " --help"))
    buf = io.StringIO()
    with contextlib.suppress(SystemExit), contextlib.redirect_stdout(buf):
        with pytest.raises(SystemExit):
            main()

    output: str = buf.getvalue()
    file_regression.check(output)
