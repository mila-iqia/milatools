"""Unit tests for the `milatools.cli.code` module.

TODO: There are quite a few tests in `tests/integration/test_code.py` that could be
moved here, since some of them aren't exactly "integration" tests.
"""

from unittest.mock import AsyncMock, Mock

import pytest

import milatools.cli.code
import milatools.cli.utils
from milatools.cli.code import code
from milatools.utils.compute_node import ComputeNode
from milatools.utils.local_v2 import LocalV2
from milatools.utils.remote_v2 import RemoteV2


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


@pytest.mark.parametrize(
    ("alloc", "expected_mem_flag"),
    [
        # No flags: should add --mem=8G
        ([], True),
        # Flags without mem: should add --mem=8G
        (["--gres=gpu:1", "--time=1:00:00"], True),
        # Flag with --mem=: should NOT add --mem=8G
        (["--mem=4G"], False),
        # Flag with --mem-per-cpu=: should NOT add --mem=8G
        (["--mem-per-cpu=2G"], False),
        # Flag with --mem-per-gpu=: should NOT add --mem=8G
        (["--mem-per-gpu=2G"], False),
    ],
    ids=["no_flags", "flags_without_mem", "mem_flag", "mem_per_cpu_flag", "mem_per_gpu_flag"],
)
@pytest.mark.asyncio
async def test_code_adds_default_mem_flag(
    monkeypatch: pytest.MonkeyPatch,
    alloc: list[str],
    expected_mem_flag: bool,
):
    """Test that --mem=8G is added to alloc when no mem flag is present."""
    import shutil

    monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/" + cmd)

    mock_login_node = AsyncMock(spec=RemoteV2)
    mock_login_node.hostname = "mila"
    mock_login_node.get_output_async = AsyncMock(return_value="/home/testuser")

    mock_remote_v2_cls = Mock(spec=RemoteV2)
    mock_remote_v2_cls.connect = AsyncMock(return_value=mock_login_node)
    monkeypatch.setattr(milatools.cli.code, RemoteV2.__name__, mock_remote_v2_cls)

    monkeypatch.setattr(
        milatools.cli.code,
        "check_disk_quota",
        AsyncMock(),
    )
    monkeypatch.setattr(
        milatools.cli.code,
        "can_access_compute_nodes",
        Mock(return_value=True),
    )
    monkeypatch.setattr(
        milatools.cli.code,
        "internet_on_compute_nodes",
        Mock(return_value=True),
    )

    captured_salloc_flags: list[str] = []

    async def _mock_salloc(login_node, salloc_flags, job_name):
        captured_salloc_flags.extend(salloc_flags)
        mock_compute_node = Mock(spec=ComputeNode)
        mock_compute_node.hostname = "cn-a001"
        mock_compute_node.job_id = 12345
        return mock_compute_node

    monkeypatch.setattr(milatools.cli.code, "salloc", _mock_salloc)

    monkeypatch.setattr(
        milatools.cli.code,
        "launch_vscode_loop",
        AsyncMock(),
    )

    await code(
        path="some/path",
        command="code",
        persist=False,
        job=None,
        node=None,
        alloc=alloc,
        cluster="mila",
    )

    if expected_mem_flag:
        assert "--mem=8G" in captured_salloc_flags
    else:
        assert "--mem=8G" not in captured_salloc_flags
