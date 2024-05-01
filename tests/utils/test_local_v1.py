from __future__ import annotations

import sys
from subprocess import PIPE

import pytest
from pytest_regressions.file_regression import FileRegressionFixture

from milatools.utils.local_v1 import CommandNotFoundError, LocalV1, check_passwordless
from milatools.utils.remote_v2 import is_already_logged_in

from ..cli.common import (
    in_github_CI,
    in_self_hosted_github_CI,
    output_tester,
    passwordless_ssh_connection_to_localhost_is_setup,
    requires_no_s_flag,
    skip_if_on_github_cloud_CI,
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


# @PARAMIKO_SSH_BANNER_BUG
# @paramiko_openssh_key_parsing_issue
@pytest.mark.xfail(
    reason="TODO: `check_passwordless` is incredibly flaky and needs to be reworked."
)
@pytest.mark.parametrize(
    ("hostname", "expected"),
    [
        pytest.param(
            "localhost",
            passwordless_ssh_connection_to_localhost_is_setup,
        ),
        ("blablabob@localhost", False),
        pytest.param(
            "mila",
            True if (in_self_hosted_github_CI or not in_github_CI) else False,
        ),
        pytest.param(
            "bobobobobobo@mila",
            False,
            marks=[
                paramiko_openssh_key_parsing_issue,
                skip_if_on_github_cloud_CI,
            ],
        ),
        # For the clusters with 2FA, we expect `check_passwordless` to return True if
        # we've already setup the shared SSH connection.
        pytest.param(
            "blablabob@narval",
            False,
            marks=[
                skip_if_on_github_cloud_CI,
                paramiko_openssh_key_parsing_issue,
            ],
        ),
        *(
            # note: can't properly test for the False case because of the 2FA
            # prompt!
            pytest.param(
                drac_cluster,
                True,
                marks=pytest.mark.skipif(
                    sys.platform == "win32" or not is_already_logged_in(drac_cluster),
                    reason="Should give True when we're already logged in.",
                ),
            )
            for drac_cluster in ["narval", "beluga", "cedar", "graham"]
        ),
        pytest.param(
            "niagara",
            False,
            marks=[
                skip_if_on_github_cloud_CI,
                paramiko_openssh_key_parsing_issue,
            ],
        ),  # SSH access to niagara isn't enabled by default.
    ],
)
def test_check_passwordless(hostname: str, expected: bool):
    # TODO: Maybe also test how `check_passwordless` behaves when using a key with a
    # passphrase.
    assert check_passwordless(hostname) == expected
