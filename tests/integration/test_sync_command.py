from __future__ import annotations

import importlib
import inspect
import subprocess
from logging import getLogger as get_logger
from typing import Callable
from unittest.mock import ANY, AsyncMock, Mock

import pytest
from typing_extensions import ParamSpec

from milatools.utils import vscode_utils
from milatools.utils.local_v2 import LocalV2
from milatools.utils.remote_v2 import RemoteV2
from milatools.utils.vscode_utils import (
    _extensions_to_install,
    _find_code_server_executable,
    _install_vscode_extensions_task_function,
    sync_vscode_extensions,
)

from ..cli.common import (
    requires_ssh_to_localhost,
)

P = ParamSpec("P")
logger = get_logger(__name__)


@pytest.mark.slow
@pytest.mark.parametrize(
    "source",
    [
        pytest.param("localhost", marks=requires_ssh_to_localhost),
        "cluster",
    ],
)
@pytest.mark.parametrize(
    "dest",
    [
        pytest.param("localhost", marks=requires_ssh_to_localhost),
        "cluster",
    ],
)
@pytest.mark.asyncio
async def test_sync_vscode_extensions(
    source: str,
    dest: str,
    cluster: str,
    login_node_v2: RemoteV2,
    monkeypatch: pytest.MonkeyPatch,
):
    if source == "cluster":
        source = cluster
    if dest == "cluster":
        dest = cluster

    if source == dest:
        pytest.skip("Source and destination are the same.")

    def mock_and_patch(wraps: Callable, *mock_args, **mock_kwargs):
        mock_kwargs = mock_kwargs.copy()
        mock_kwargs["wraps"] = wraps
        _mock_type = AsyncMock if inspect.iscoroutinefunction(wraps) else Mock
        mock = _mock_type(*mock_args, **mock_kwargs)
        module = importlib.import_module(wraps.__module__)
        monkeypatch.setattr(module, wraps.__name__, mock)
        return mock

    mock_task_function = mock_and_patch(
        wraps=_install_vscode_extensions_task_function,
    )
    extension, version = "ms-python.python", "v2024.0.1"

    # Make it so we only need to install this particular extension.
    mock_extensions_to_install = mock_and_patch(
        wraps=_extensions_to_install,
        return_value={extension: version},
    )
    mock_find_code_server_executable = mock_and_patch(
        wraps=_find_code_server_executable,
    )
    from milatools.utils.vscode_utils import _install_vscode_extension

    mock_install_extension = AsyncMock(
        spec=_install_vscode_extension,
        return_value=subprocess.CompletedProcess(
            args=["..."],
            returncode=0,
            stdout=f"Successfully installed {extension}@{version}",
        ),
    )
    monkeypatch.setattr(
        vscode_utils, _install_vscode_extension.__name__, mock_install_extension
    )

    # Avoid actually installing this (possibly oudated?) extension.
    extensions_per_cluster = await sync_vscode_extensions(
        source=LocalV2() if source == "localhost" else login_node_v2,
        destinations=[dest],
    )
    assert extensions_per_cluster == {dest: [f"{extension}@{version}"]}

    mock_install_extension.assert_called_once_with(
        LocalV2() if dest == "localhost" else login_node_v2,
        code_server_executable=ANY,
        extension=f"{extension}@{version}",
        verbose=ANY,
    )

    mock_task_function.assert_called_once()
    mock_extensions_to_install.assert_called_once()
    if source == "localhost":
        mock_find_code_server_executable.assert_called_once_with(
            RemoteV2(dest), remote_vscode_server_dir="~/.vscode-server"
        )
    elif dest == "localhost":
        mock_find_code_server_executable.assert_called_once_with(
            RemoteV2(source), remote_vscode_server_dir="~/.vscode-server"
        )
    else:
        assert len(mock_find_code_server_executable.mock_calls) == 2
        mock_find_code_server_executable.assert_any_call(
            RemoteV2(source), remote_vscode_server_dir="~/.vscode-server"
        )
        mock_find_code_server_executable.assert_any_call(
            RemoteV2(dest), remote_vscode_server_dir="~/.vscode-server"
        )
