from __future__ import annotations

import subprocess
from subprocess import PIPE
from unittest import mock

import pytest
from pytest_mock import MockerFixture
from pytest_regressions.file_regression import FileRegressionFixture

from milatools.cli.local import CommandNotFoundError, Local

from .common import output_tester, requires_no_s_flag, xfails_on_windows
from .test_remote import passwordless_ssh_connection_to_localhost_is_setup

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
    output_tester(lambda: (Local().display(cmd), None), capsys, file_regression)


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
    output_tester(lambda: (Local().silent_get(*cmd), None), capsys, file_regression)


@prints_unexpected_text_to_stdout_on_windows
@requires_no_s_flag
@pytest.mark.parametrize("cmd", [_ECHO_CMD])
def test_get(
    cmd: list[str],
    capsys: pytest.CaptureFixture,
    file_regression: FileRegressionFixture,
):
    output_tester(lambda: (Local().get(*cmd), None), capsys, file_regression)


@prints_unexpected_text_to_stdout_on_windows
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


@prints_unexpected_text_to_stdout_on_windows
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


def test_check_passwordless(mocker: MockerFixture):
    mock_subprocess_run: mock.Mock = mocker.patch(
        "subprocess.run", wraps=subprocess.run
    )
    hostname = "localhost"
    local = Local()
    result = local.check_passwordless(hostname)
    mock_subprocess_run.assert_called_once()
    assert result == passwordless_ssh_connection_to_localhost_is_setup


def test_check_passwordless_permission_denied(mocker: MockerFixture):
    mock_subprocess_run: mock.Mock = mocker.patch(
        "subprocess.run", wraps=subprocess.run
    )
    hostname = "blablabob@localhost"
    local = Local()
    result = local.check_passwordless(hostname)
    mock_subprocess_run.assert_called_once()
    assert result is False


@pytest.mark.parametrize("output", [None, "some unexpected output"])
def test_check_passwordless_timeout(mocker: MockerFixture, output: str | None):
    mock_subprocess_run: mock.Mock = mocker.patch(
        "subprocess.run",
        spec=subprocess.run,
        side_effect=subprocess.TimeoutExpired("ssh", 1, output=output),
    )
    local = Local()
    result = local.check_passwordless("doesnt_matter", timeout=1)
    mock_subprocess_run.assert_called_once()
    assert result is False


def test_check_passwordless_weird_output(mocker: MockerFixture):
    mock_subprocess_run: mock.Mock = mocker.patch(
        "subprocess.run",
        spec=subprocess.run,
        side_effect=[
            subprocess.CompletedProcess(
                args=["ssh", "..."], returncode=0, stdout="something unexpected"
            )
        ],
    )
    hostname = "blablabob@localhost"
    local = Local()
    result = local.check_passwordless(hostname)
    mock_subprocess_run.assert_called_once()
    assert result is False
