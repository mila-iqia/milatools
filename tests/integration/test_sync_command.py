from __future__ import annotations

import importlib
from logging import getLogger as get_logger
from typing import Callable
from unittest.mock import Mock

import pytest
from typing_extensions import ParamSpec

from milatools.utils.local_v1 import LocalV1
from milatools.utils.remote_v2 import RemoteV2
from milatools.utils.vscode_utils import (
    extensions_to_install,
    find_code_server_executable,
    install_vscode_extensions_task_function,
    sync_vscode_extensions,
)

from ..cli.common import (
    requires_ssh_to_localhost,
)

P = ParamSpec("P")
logger = get_logger(__name__)


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
def test_sync_vscode_extensions(
    source: str,
    dest: str,
    cluster: str,
    monkeypatch: pytest.MonkeyPatch,
):
    if source == "cluster":
        source = cluster
    if dest == "cluster":
        dest = cluster

    if source == dest:
        pytest.skip("Source and destination are the same.")

    def _mock_and_patch(
        wraps: Callable,
        _mock_type: Callable[P, Mock] = Mock,
        *mock_args: P.args,
        **mock_kwargs: P.kwargs,
    ):
        mock_kwargs["wraps"] = wraps
        mock = _mock_type(*mock_args, **mock_kwargs)
        module = importlib.import_module(wraps.__module__)
        monkeypatch.setattr(module, wraps.__name__, mock)
        return mock

    mock_task_function = _mock_and_patch(wraps=install_vscode_extensions_task_function)
    # Make it so we only need to install this particular extension.
    mock_extensions_to_install = _mock_and_patch(
        wraps=extensions_to_install, return_value={"ms-python.python": "v2024.0.1"}
    )
    mock_find_code_server_executable = _mock_and_patch(
        wraps=find_code_server_executable,
    )

    sync_vscode_extensions(
        source=LocalV1() if source == "localhost" else RemoteV2(source),
        dest_clusters=[dest],
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
