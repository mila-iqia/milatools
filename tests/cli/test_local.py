from contextlib import contextmanager
from subprocess import PIPE, CompletedProcess

import pytest

from milatools.cli.local import CommandNotFoundError, Local

_ECHO_CMD = ["echo", "--arg1", "val1", "--arg2=val2", "X"]
_FAKE_CMD = ["FAKEcmd", "--arg1", "val1", "--arg2=val2", "X"]
_FAIL_CODE_CMD = ["FAKEcode", "--arg1", "val1", "--arg2=val2", "X"]


def _test_stdouterr(func, capsys, file_regression):
    out, err = None, None
    try:
        out, err = func()
        if isinstance(out, CompletedProcess):
            out, err = out.stdout, out.stderr
    finally:
        captured = capsys.readouterr()
        out = out if out else ""
        err = err if err else ""
        file_regression.check(
            f"{captured.out}:::::\n{captured.err}=====\n{out}^^^^^\n{err}^^^^^\n"
        )


@pytest.mark.parametrize("cmd", [_ECHO_CMD, _FAKE_CMD])
def test_display(cmd, capsys, file_regression):
    _test_stdouterr(lambda: (Local().display(cmd), None), capsys, file_regression)


@pytest.mark.parametrize("cmd", [_ECHO_CMD])
def test_silent_get(cmd, capsys, file_regression):
    _test_stdouterr(lambda: (Local().silent_get(*cmd), None), capsys, file_regression)


@pytest.mark.parametrize("cmd", [_ECHO_CMD])
def test_get(cmd, capsys, file_regression):
    _test_stdouterr(lambda: (Local().get(*cmd), None), capsys, file_regression)


@pytest.mark.parametrize("cmd", [_ECHO_CMD, _FAKE_CMD, _FAIL_CODE_CMD])
def test_run(cmd, capsys, file_regression):
    func = lambda: (Local().run(*cmd, capture_output=True), None)
    if cmd in [_FAKE_CMD, _FAIL_CODE_CMD]:

        def _catch_exc():
            with pytest.raises(CommandNotFoundError) as exc_info:
                func()
            exc_info.value.args = (
                exc_info.value.args[0].replace("FAKE", ""),
                *exc_info.value.args[1:],
            )
            return None, f"{exc_info.value}\n"

        _test_stdouterr(_catch_exc, capsys, file_regression)
    else:
        _test_stdouterr(func, capsys, file_regression)


@pytest.mark.parametrize("cmd", [_ECHO_CMD])
def test_popen(cmd, capsys, file_regression):
    _test_stdouterr(
        lambda: Local().popen(*cmd, stdout=PIPE, stderr=PIPE).communicate(),
        capsys,
        file_regression,
    )
