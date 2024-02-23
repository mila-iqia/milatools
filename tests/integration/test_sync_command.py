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
    sync_vscode_extensions_in_parallel,
)
from milatools.remote_v2 import RemoteV2
from tests.integration.conftest import skip_param_if_not_already_logged_in

from ..cli.common import (
    requires_ssh_to_localhost,
)

P = ParamSpec("P")
logger = get_logger(__name__)


@pytest.mark.parametrize(
    "source",
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
@pytest.mark.parametrize(
    "dest",
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
def test_sync_vscode_extensions(
    source: str,
    dest: str,
    monkeypatch: pytest.MonkeyPatch,
):
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
        monkeypatch.setattr(
            importlib.import_module(wraps.__module__), wraps.__name__, mock
        )
        return mock

    mock_task_function = _mock_and_patch(
        wraps=_install_vscode_extensions_task_function,
        # side_effect=functools.partial(
        #     _install_vscode_extensions_task_function, verbose=True
        # ),
    )
    # Make it so we only need to install this particular extension.
    # missing_extensions = {}
    _mock_extensions_to_install = _mock_and_patch(
        wraps=extensions_to_install, return_value={"ms-python.python": "v2024.0.1"}
    )

    mock_find_code_server_executable = _mock_and_patch(
        wraps=find_code_server_executable,
    )

    sync_vscode_extensions_in_parallel(
        source=Local() if source == "localhost" else RemoteV2(source),
        dest_clusters=[dest],
    )

    mock_task_function.assert_called_once()
    _mock_extensions_to_install.assert_called_once()
    mock_find_code_server_executable.assert_called_once()
