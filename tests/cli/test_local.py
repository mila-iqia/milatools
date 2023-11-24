from __future__ import annotations

from subprocess import PIPE

import pytest
from pytest_regressions.file_regression import FileRegressionFixture

from milatools.cli.local import CommandNotFoundError, Local

from .common import output_tester, requires_no_s_flag

_ECHO_CMD = ["echo", "--arg1", "val1", "--arg2=val2", "X"]
_FAKE_CMD = ["FAKEcmd", "--arg1", "val1", "--arg2=val2", "X"]
_FAIL_CODE_CMD = ["FAKEcode", "--arg1", "val1", "--arg2=val2", "X"]


@requires_no_s_flag
@pytest.mark.parametrize("cmd", [_ECHO_CMD, _FAKE_CMD])
def test_display(
    cmd: list[str],
    capsys: pytest.CaptureFixture,
    file_regression: FileRegressionFixture,
):
    output_tester(lambda: (Local().display(cmd), None), capsys, file_regression)


@pytest.mark.parametrize("cmd", [_ECHO_CMD])
def test_silent_get(
    cmd: list[str],
    capsys: pytest.CaptureFixture,
    file_regression: FileRegressionFixture,
):
    output_tester(lambda: (Local().silent_get(*cmd), None), capsys, file_regression)


@requires_no_s_flag
@pytest.mark.parametrize("cmd", [_ECHO_CMD])
def test_get(
    cmd: list[str],
    capsys: pytest.CaptureFixture,
    file_regression: FileRegressionFixture,
):
    output_tester(lambda: (Local().get(*cmd), None), capsys, file_regression)


@requires_no_s_flag
@pytest.mark.parametrize("cmd", [_ECHO_CMD, _FAKE_CMD, _FAIL_CODE_CMD])
def test_run(
    cmd: list[str],
    capsys: pytest.CaptureFixture,
    file_regression: FileRegressionFixture,
):
    def func():
        return Local().run(*cmd, capture_output=True), None

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


@requires_no_s_flag
@pytest.mark.parametrize("cmd", [_ECHO_CMD])
def test_popen(
    cmd: list[str],
    capsys: pytest.CaptureFixture,
    file_regression: FileRegressionFixture,
):
    output_tester(
        lambda: Local().popen(*cmd, stdout=PIPE, stderr=PIPE).communicate(),
        capsys,
        file_regression,
    )
