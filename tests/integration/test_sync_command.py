from __future__ import annotations

import importlib
from logging import getLogger as get_logger
from typing import Callable
from unittest.mock import Mock

import pytest
from typing_extensions import ParamSpec

from milatools.cli.local import Local
from milatools.cli.vscode_utils import (
    _install_vscode_extensions_task_function,
    extensions_to_install,
    find_code_server_executable,
    get_code_command,
    sync_vscode_extensions_in_parallel,
)

from ..cli.common import (
    requires_ssh_to_localhost,
)

P = ParamSpec("P")
logger = get_logger(__name__)


def mock_extensions_to_install(
    source_extensions: dict[str, str],
    dest_extensions: dict[str, str],
    source_name: str,
    dest_name: str,
):
    return source_extensions


@requires_ssh_to_localhost
def test_sync_vscode_extensions(monkeypatch: pytest.MonkeyPatch):
    def _mock_and_patch(
        wraps: Callable,
        _mock_type: Callable[P, Mock] = Mock,
        *mock_args: P.args,
        **mock_kwargs: P.kwargs,
    ):
        mock_kwargs["wraps"] = wraps
        mock = _mock_type(*mock_args, **mock_kwargs)
        monkeypatch.setattr(
            importlib.import_module(wraps.__module__), wraps.__name__, mock
        )
        return mock

    mock_task_function = _mock_and_patch(
        wraps=_install_vscode_extensions_task_function,
    )
    # Make it so we only need to install this extension.
    _mock_extensions_to_install = _mock_and_patch(
        wraps=extensions_to_install, side_effect=mock_extensions_to_install
    )
    # Use code --install-extension instead of `code-server --install-extension`
    mock_find_code_server_executable = _mock_and_patch(
        wraps=find_code_server_executable, return_value=get_code_command()
    )
    sync_vscode_extensions_in_parallel(source=Local(), dest_clusters=[Local()])
    mock_task_function.assert_called_once()
    _mock_extensions_to_install.assert_called_once()
    mock_find_code_server_executable.assert_called_once()
