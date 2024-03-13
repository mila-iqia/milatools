from pathlib import Path
from unittest.mock import Mock

import pytest

import milatools.utils.remote_v2
from milatools.utils.remote_v2 import (
    RemoteV2,
    UnsupportedPlatformError,
    control_socket_is_running,
    get_controlpath_for,
    is_already_logged_in,
)
from tests.integration.conftest import skip_param_if_not_already_logged_in

from ..cli.common import requires_ssh_to_localhost, xfails_on_windows

pytestmark = [xfails_on_windows(raises=UnsupportedPlatformError, strict=True)]


@requires_ssh_to_localhost
def test_init_with_controlpath(tmp_path: Path):
    control_path = tmp_path / "socketfile"
    remote = RemoteV2("localhost", control_path=control_path)
    assert control_path.exists()
    files = remote.get_output(f"ls {control_path.parent}").split()
    assert files == [control_path.name]


@requires_ssh_to_localhost
def test_init_with_none_controlpath(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    control_path = tmp_path / "socketfile"
    mock_get_controlpath_for = Mock(
        wraps=get_controlpath_for, return_value=control_path
    )

    monkeypatch.setattr(
        milatools.utils.remote_v2,
        get_controlpath_for.__name__,
        mock_get_controlpath_for,
    )
    remote = RemoteV2("localhost", control_path=None)
    mock_get_controlpath_for.assert_called_once_with("localhost")
    assert control_path.exists()
    files = remote.get_output(f"ls {control_path.parent}").split()
    assert files == [control_path.name]


@pytest.mark.parametrize(
    "hostname",
    [
        pytest.param("localhost", marks=requires_ssh_to_localhost),
        skip_param_if_not_already_logged_in("mila"),
        skip_param_if_not_already_logged_in("narval"),
        skip_param_if_not_already_logged_in("beluga"),
        skip_param_if_not_already_logged_in("cedar"),
        skip_param_if_not_already_logged_in("graham"),
        skip_param_if_not_already_logged_in("niagara"),
    ],
)
def test_run(hostname: str):
    command = "echo Hello World"
    remote = RemoteV2(hostname)
    output = remote.get_output(command)
    assert output == "Hello World"


# NOTE: The timeout here is a part of the test: if we are already connected, running the
# command should be fast, and if we aren't connected, this should be able to tell fast
# (in other words, it shouldn't wait for 2FA input or similar).
@pytest.mark.timeout(1, func_only=True)
@pytest.mark.parametrize("also_run_command_to_check", [False, True])
def test_is_already_logged_in(
    cluster: str, already_logged_in: bool, also_run_command_to_check: bool
):
    assert (
        is_already_logged_in(
            cluster, also_run_command_to_check=also_run_command_to_check
        )
        == already_logged_in
        == get_controlpath_for(cluster).exists()
    )


def test_controlsocket_is_running(cluster: str, already_logged_in: bool):
    control_path = get_controlpath_for(cluster)
    assert control_socket_is_running(cluster, control_path) == already_logged_in
