from __future__ import annotations

from subprocess import PIPE

import pytest
from pytest_regressions.file_regression import FileRegressionFixture

from milatools.utils.local_v1 import CommandNotFoundError, LocalV1

from ..cli.common import (
    in_github_CI,
    in_self_hosted_github_CI,
    output_tester,
    requires_no_s_flag,
    xfails_on_windows,
)

_ECHO_CMD = pytest.param(
    ["echo", "--arg1", "val1", "--arg2=val2", "X"],
    marks=xfails_on_windows(
        raises=FileNotFoundError,
        strict=False,
        reason="`echo` command isn't available on Windows.",
    ),
)
_FAKE_CMD = ["FAKEcmd", "--arg1", "val1", "--arg2=val2", "X"]
_FAIL_CODE_CMD = ["FAKEcode", "--arg1", "val1", "--arg2=val2", "X"]


@requires_no_s_flag
@pytest.mark.parametrize("cmd", [_ECHO_CMD, _FAKE_CMD])
def test_display(
    cmd: list[str],
    capsys: pytest.CaptureFixture,
    file_regression: FileRegressionFixture,
):
    output_tester(lambda: (LocalV1().display(cmd), None), capsys, file_regression)


prints_unexpected_text_to_stdout_on_windows = xfails_on_windows(
    raises=AssertionError,
    strict=False,
    reason=(
        "BUG: There is somehow some text being printed to stdout during this test on "
        "windows."
    ),
)


@prints_unexpected_text_to_stdout_on_windows
@pytest.mark.parametrize("cmd", [_ECHO_CMD])
def test_silent_get(
    cmd: list[str],
    capsys: pytest.CaptureFixture,
    file_regression: FileRegressionFixture,
):
    output_tester(lambda: (LocalV1().silent_get(*cmd), None), capsys, file_regression)


@prints_unexpected_text_to_stdout_on_windows
@requires_no_s_flag
@pytest.mark.parametrize("cmd", [_ECHO_CMD])
def test_get(
    cmd: list[str],
    capsys: pytest.CaptureFixture,
    file_regression: FileRegressionFixture,
):
    output_tester(lambda: (LocalV1().get(*cmd), None), capsys, file_regression)


@prints_unexpected_text_to_stdout_on_windows
@requires_no_s_flag
@pytest.mark.parametrize("cmd", [_ECHO_CMD, _FAKE_CMD, _FAIL_CODE_CMD])
def test_run(
    cmd: list[str],
    capsys: pytest.CaptureFixture,
    file_regression: FileRegressionFixture,
):
    def func():
        return LocalV1().run(*cmd, capture_output=True), None

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


@prints_unexpected_text_to_stdout_on_windows
@requires_no_s_flag
@pytest.mark.parametrize("cmd", [_ECHO_CMD])
def test_popen(
    cmd: list[str],
    capsys: pytest.CaptureFixture,
    file_regression: FileRegressionFixture,
):
    output_tester(
        lambda: LocalV1().popen(*cmd, stdout=PIPE, stderr=PIPE).communicate(),
        capsys,
        file_regression,
    )


paramiko_openssh_key_parsing_issue = pytest.mark.xfail(
    # Expect this to sometimes fail, except when we're in the (cloud) GitHub CI.
    not in_github_CI or in_self_hosted_github_CI,
    strict=False,
    raises=ValueError,
    # ValueError("q must be exactly 160, 224, or 256 bits long")
    # https://github.com/paramiko/paramiko/issues/1839
    # https://github.com/fabric/fabric/issues/2182
    # https://github.com/paramiko/paramiko/pull/1606
    reason=(
        "BUG: Seems like paramiko reads new RSA keys of OpenSSH as DSA "
        "and raises a ValueError."
    ),
)
