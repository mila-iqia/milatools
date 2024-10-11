"""Unit tests for the `milatools.cli.code` module.

TODO: There are quite a few tests in `tests/integration/test_code.py` that could be
moved here, since some of them aren't exactly "integration" tests.
"""

import sys
from unittest.mock import AsyncMock, Mock

import pytest

import milatools.cli.code
import milatools.cli.utils
from milatools.cli.utils import running_inside_WSL
from milatools.utils.compute_node import ComputeNode
from milatools.utils.local_v2 import LocalV2
from milatools.utils.remote_v2 import UnsupportedPlatformError


@pytest.fixture
def pretend_to_be_in_WSL(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
):
    # By default, pretend to be in WSL. Indirect parametrization can be used to
    # overwrite this value for a given test (as is done below).
    in_wsl = getattr(request, "param", True)

    _mock_running_inside_WSL = Mock(spec=running_inside_WSL, return_value=in_wsl)
    monkeypatch.setattr(
        milatools.cli.utils,
        running_inside_WSL.__name__,  # type: ignore
        _mock_running_inside_WSL,
    )
    monkeypatch.setattr(
        milatools.cli.code,
        running_inside_WSL.__name__,  # type: ignore
        _mock_running_inside_WSL,
    )
    return in_wsl


@pytest.mark.xfail(
    sys.platform == "win32",
    raises=UnsupportedPlatformError,
    reason="Uses RemoteV2, so isn't supported on Windows.",
    strict=True,
)
@pytest.mark.parametrize("pretend_to_be_in_WSL", [True, False], indirect=True)
@pytest.mark.asyncio
async def test_code_from_WSL(
    monkeypatch: pytest.MonkeyPatch, pretend_to_be_in_WSL: bool
):
    # Mock the LocalV2 class so that we can inspect the call to `LocalV2.run_async`.
    mock_localv2 = Mock(spec=LocalV2)
    monkeypatch.setattr(milatools.cli.code, LocalV2.__name__, mock_localv2)

    await milatools.cli.code.launch_vscode_loop(
        "code", Mock(spec=ComputeNode, hostname="foo"), "/bob/path"
    )
    assert isinstance(mock_localv2.run_async, AsyncMock)
    mock_localv2.run_async.assert_called_once_with(
        (
            *(("powershell.exe",) if pretend_to_be_in_WSL else ()),
            "code",
            "--new-window",
            "--wait",
            "--remote",
            "ssh-remote+foo",
            "/bob/path",
        ),
        display=True,
    )
