from pathlib import Path
from unittest.mock import Mock

import pytest

import milatools.remote_v2
from milatools.remote_v2 import RemoteV2, get_controlpath_for

from .cli.common import requires_ssh_to_localhost


@requires_ssh_to_localhost
def test_remotev2_with_controlpath(tmp_path: Path):
    control_path = tmp_path / "socketfile"
    remote = RemoteV2("localhost", control_path=control_path)
    assert control_path.exists()
    files = remote.get_output(f"ls {control_path.parent}").split()
    assert files == [control_path.name]


@requires_ssh_to_localhost
def test_remotev2_no_controlpath_passed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
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
