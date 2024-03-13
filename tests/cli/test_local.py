from __future__ import annotations

from subprocess import PIPE

import pytest
from pytest_regressions.file_regression import FileRegressionFixture

from milatools.cli.local import CommandNotFoundError, Local, check_passwordless

from .common import (
    in_github_CI,
    output_tester,
    passwordless_ssh_connection_to_localhost_is_setup,
    requires_no_s_flag,
    in_self_hosted_github_CI,
    output_tester,
    passwordless_ssh_connection_to_localhost_is_setup,
    requires_no_s_flag,
    skip_if_on_github_CI,
    skip_param_if_on_github_ci,
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


def paramiko_openssh_key_parsing_issue(strict: bool = False):
    return pytest.mark.xfail(
        not in_github_CI,
        strict=strict,
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


re_enable_once_remotev2_is_used = pytest.mark.skipif(
    in_self_hosted_github_CI,
    reason="TODO: Might go through 2FA, re-enable once we use RemoteV2",
)


@pytest.mark.parametrize(
    ("hostname", "expected"),
    [
        ("localhost", passwordless_ssh_connection_to_localhost_is_setup),
        ("blablabob@localhost", False),
        pytest.param(
            "mila",
            not in_github_CI,
            marks=pytest.mark.xfail(
                reason="TODO: Paramiko SSH protocol banner issue...", strict=False
            ),
        ),
        pytest.param(
            "bobobobobobo@mila",
            False,
            marks=[
                skip_if_on_github_CI,
                paramiko_openssh_key_parsing_issue(),
            ],
        ),
        # For the clusters with 2FA, we expect `check_passwordless` to return True if
        # we've already setup the shared SSH connection.
        pytest.param(
            "blablabob@narval",
            False,
            marks=[
                skip_if_on_github_CI,
                paramiko_openssh_key_parsing_issue(),
            ],
        ),
        *(
            pytest.param(drac_cluster, True, marks=re_enable_once_remotev2_is_used)
            for drac_cluster in ["narval", "beluga", "cedar", "graham"]
        ),
        pytest.param(
            "niagara",
            False,
            marks=[skip_if_on_github_CI, paramiko_openssh_key_parsing_issue()],
        ),  # SSH access isn't enabled by default.
    ],
)
def test_check_passwordless(hostname: str, expected: bool):
    # TODO: Maybe also test how `check_passwordless` behaves when using a key with a
    # passphrase.
    assert check_passwordless(hostname) == expected
