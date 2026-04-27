"""Unit tests for the `milatools.cli.code` module.

TODO: There are quite a few tests in `tests/integration/test_code.py` that could be
moved here, since some of them aren't exactly "integration" tests.
"""

from unittest.mock import AsyncMock, Mock

import pytest

import milatools.cli.code
import milatools.cli.utils
from milatools.utils.compute_node import ComputeNode
from milatools.utils.local_v2 import LocalV2


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
