"""Unit tests for the `milatools.cli.code` module.

TODO: There are quite a few tests in `tests/integration/test_code.py` that could be
moved here, since some of them aren't exactly "integration" tests.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

import milatools.cli.code
import milatools.cli.utils
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
    ("job", "persist"),
    [(None, False), (None, True), (123, True)],
    ids=["salloc", "sbatch", "existing_job"],
)
@pytest.mark.asyncio
async def test_setup_slurm_env_hook_called_only_when_creating_a_job(
    monkeypatch: pytest.MonkeyPatch, job: int | None, persist: bool
):
    """The SLURM env forwarding is set up on the salloc/sbatch paths only.

    The `--job`/`--node` path connects to an existing job, which milatools didn't
    create and whose environment therefore wasn't dumped.
    """
    code_module = milatools.cli.code

    mock_login_node = Mock(spec=RemoteV2, hostname="mila")
    mock_login_node.configure_mock(
        get_output_async=AsyncMock(
            spec=RemoteV2.get_output_async, return_value="/home/mila/u/user"
        ),
    )
    mock_remote_v2 = Mock(spec=RemoteV2)
    mock_remote_v2.connect = AsyncMock(return_value=mock_login_node)
    mock_compute_node = Mock(spec=ComputeNode, hostname="cn-a001", job_id=job or 456)

    mock_compute_node_cls = Mock(spec=ComputeNode)
    mock_compute_node_cls.connect = AsyncMock(return_value=mock_compute_node)

    monkeypatch.setattr(
        code_module, "shutil", Mock(which=Mock(return_value="/usr/bin/code"))
    )
    monkeypatch.setattr(code_module, RemoteV2.__name__, mock_remote_v2)
    monkeypatch.setattr(code_module, "check_disk_quota", AsyncMock())
    monkeypatch.setattr(code_module, "SSHConfig", Mock())
    monkeypatch.setattr(code_module, "get_ssh_public_key_path", Mock(return_value=None))
    monkeypatch.setattr(
        code_module, "can_access_compute_nodes", Mock(return_value=True)
    )
    monkeypatch.setattr(
        code_module, "internet_on_compute_nodes", Mock(return_value=True)
    )
    monkeypatch.setattr(
        code_module, "salloc", mock_salloc := AsyncMock(return_value=mock_compute_node)
    )
    monkeypatch.setattr(
        code_module, "sbatch", mock_sbatch := AsyncMock(return_value=mock_compute_node)
    )
    monkeypatch.setattr(code_module, ComputeNode.__name__, mock_compute_node_cls)
    monkeypatch.setattr(code_module, "launch_vscode_loop", AsyncMock())
    monkeypatch.setattr(
        code_module, "setup_slurm_env_hook", mock_setup_hook := AsyncMock()
    )

    await code_module.code(
        path="bob",
        command="code",
        persist=persist,
        job=job,
        node=None,
        alloc=[],
        cluster="mila",
    )

    if job is None:
        mock_setup_hook.assert_awaited_once_with(mock_login_node)
        if persist:
            mock_sbatch.assert_awaited_once()
            mock_salloc.assert_not_called()
        else:
            mock_salloc.assert_awaited_once()
            mock_sbatch.assert_not_called()
    else:
        mock_setup_hook.assert_not_called()
        mock_compute_node_cls.connect.assert_awaited_once()
        mock_salloc.assert_not_called()
        mock_sbatch.assert_not_called()
