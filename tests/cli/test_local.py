from subprocess import PIPE

import pytest

from milatools.cli.local import CommandNotFoundError, Local

from .common import output_tester

_ECHO_CMD = ["echo", "--arg1", "val1", "--arg2=val2", "X"]
_FAKE_CMD = ["FAKEcmd", "--arg1", "val1", "--arg2=val2", "X"]
_FAIL_CODE_CMD = ["FAKEcode", "--arg1", "val1", "--arg2=val2", "X"]


@pytest.mark.parametrize("cmd", [_ECHO_CMD, _FAKE_CMD])
def test_display(cmd, capsys, file_regression):
    output_tester(lambda: (Local().display(cmd), None), capsys, file_regression)


@pytest.mark.parametrize("cmd", [_ECHO_CMD])
def test_silent_get(cmd, capsys, file_regression):
    output_tester(lambda: (Local().silent_get(*cmd), None), capsys, file_regression)


@pytest.mark.parametrize("cmd", [_ECHO_CMD])
def test_get(cmd, capsys, file_regression):
    output_tester(lambda: (Local().get(*cmd), None), capsys, file_regression)


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

        output_tester(_catch_exc, capsys, file_regression)
    else:
        output_tester(func, capsys, file_regression)


@pytest.mark.parametrize("cmd", [_ECHO_CMD])
def test_popen(cmd, capsys, file_regression):
    output_tester(
        lambda: Local().popen(*cmd, stdout=PIPE, stderr=PIPE).communicate(),
        capsys,
        file_regression,
    )
