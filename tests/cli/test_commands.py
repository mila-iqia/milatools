import contextlib
import importlib
import io
import shlex
import subprocess
from subprocess import CompletedProcess
from typing import Callable
from unittest import mock

import pytest
from fabric.testing.base import Command, MockChannel, Session  # noqa
from fabric.testing.fixtures import Connection, MockRemote, remote  # noqa
from pytest_mock import MockerFixture
from pytest_regressions.file_regression import FileRegressionFixture
from typing_extensions import ParamSpec

from milatools.cli.commands import main
from milatools.cli.local import Local


def _convert_argparse_output_to_pre_py311_format(output: str) -> str:
    """Revert the slight change in the output of argparse in python 3.11."""
    return output.replace("options:\n", "optional arguments:\n")


P = ParamSpec("P")


class _MockRemote(MockRemote):
    # NOTE: This class isn't actually used. It's just here so we can see what
    # the signature of the test methods are.
    def expect(
        self,
        __session_fn: Callable[P, Session] = Session,
        *session_args: P.args,
        **session_kwargs: P.kwargs,
    ) -> MockChannel:
        return super().expect(*session_args, **session_kwargs)


def reload_module():
    """Reload the module after mocking out functions.

    Need to reload the module because we have mocked some of the functions that
    are used as default values in the methods of the class under test (e.g.
    `subprocess.run`). The functions being in the signature of the methods
    makes it possible for us to describe their our method signatures
    explicitly, but it means that we need to reload the module after mocking
    them.
    """
    global Local
    import milatools.cli.local

    importlib.reload(milatools.cli.local)
    Local = milatools.cli.local.Local


@pytest.fixture
def mock_subprocess_run(mocker: MockerFixture) -> mock.Mock:
    mock_subprocess_run: mock.Mock = mocker.patch("subprocess.run")
    # NOTE: Trying out https://stackoverflow.com/questions/25692440/mocking-a-subprocess-call-in-python

    reload_module()
    return mock_subprocess_run


def test_check_passwordless(
    mock_subprocess_run: mock.Mock,
):
    # NOTE: Trying out https://stackoverflow.com/questions/25692440/mocking-a-subprocess-call-in-python
    mock_subprocess_run.return_value = CompletedProcess(
        ["echo OK"], 0, stdout="BOBOBO"
    )
    local = Local()
    local.check_passwordless("mila")

    mock_subprocess_run.assert_called_once_with(
        shlex.split("ssh -oPreferredAuthentications=publickey mila 'echo OK'"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )


# def test_remote(remote: _MockRemote):
#     some_message = "BOBOBOB"
#     remote.expect(
#         host="login.server.mila.quebec",
#         user="normandf",
#         port=2222,
#         commands=[Command("echo OK", out=some_message.encode())],
#     )
#     result = Connection("mila").run("echo OK")
#     assert result.stdout == some_message


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
    """Test that we get a proper output when we use an invalid command (that
    exits immediately)."""
    monkeypatch.setattr("sys.argv", shlex.split(command))
    buf = io.StringIO()
    with contextlib.suppress(SystemExit), pytest.raises(
        SystemExit
    ), contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        main()
    file_regression.check(
        _convert_argparse_output_to_pre_py311_format(buf.getvalue())
    )


# TODO: Perhaps we could use something like this so we can run all tests
# locally, but skip the ones that need to actually connect to the cluster when
# running on GitHub Actions.
# def dont_run_on_github(*args):
#     return pytest.param(
#         *args,
#         marks=pytest.mark.skipif(
#             "GITHUB_ACTIONS" in os.environ,
#             reason="We don't run this test on GitHub Actions.",
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
    """Run simple commands and check that their output matches what's expected."""

    monkeypatch.setattr("webbrowser.open", lambda url: None)
    monkeypatch.setattr("sys.argv", shlex.split(command))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        main()
    output: str = buf.getvalue()
    file_regression.check(_convert_argparse_output_to_pre_py311_format(output))
