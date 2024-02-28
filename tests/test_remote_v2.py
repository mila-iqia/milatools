from pathlib import Path
from unittest.mock import Mock

import pytest

import milatools.remote_v2
from milatools.remote_v2 import RemoteV2, get_controlpath_for, is_already_logged_in
from tests.integration.conftest import skip_param_if_not_already_logged_in

from .cli.common import requires_ssh_to_localhost


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
        milatools.remote_v2,
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


def test_is_already_logged_in(cluster: str, already_logged_in: bool):
    assert is_already_logged_in(cluster) == already_logged_in
