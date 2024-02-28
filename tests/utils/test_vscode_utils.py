from __future__ import annotations

import getpass
import multiprocessing
import shutil
import sys
from multiprocessing.managers import DictProxy
from pathlib import Path
from unittest.mock import Mock

import pytest

from milatools.cli.local import Local
from milatools.cli.utils import running_inside_WSL
from milatools.utils.parallel_progress import ProgressDict
from milatools.utils.remote_v2 import UnsupportedPlatformError
from milatools.utils.vscode_utils import (
    find_code_server_executable,
    get_code_command,
    get_expected_vscode_settings_json_path,
    get_local_vscode_extensions,
    get_vscode_executable_path,
    install_vscode_extensions_task_function,
    sync_vscode_extensions_in_parallel,
    sync_vscode_extensions_in_parallel_with_hostnames,
    vscode_installed,
)

from ..cli.common import in_github_CI, requires_ssh_to_localhost, xfails_on_windows


def test_vscode_installed():
    """Check that on a dev machine with VsCode installed, this function returns True."""
    if in_github_CI:
        assert not vscode_installed()
    else:
        assert vscode_installed()


@pytest.mark.parametrize("custom_code_command", ["ls", "fake-code-doesnt-exist"])
def test_vscode_installed_with_env_var(
    custom_code_command: str, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("MILATOOLS_CODE_COMMAND", custom_code_command)
    assert get_code_command() == custom_code_command
    assert vscode_installed() == bool(shutil.which(custom_code_command))


test_requires_vscode = pytest.mark.xfail(
    not vscode_installed(),
    reason="This machine doesn't have VsCode installed.",
    strict=True,
)


@test_requires_vscode
def test_get_expected_vscode_settings_json_path():
    """Check that on a dev machine with VsCode the expected path points to a real
    file."""
    expected_path = get_expected_vscode_settings_json_path()
    print(expected_path)
    assert expected_path.exists()


def test_running_inside_WSL():
    if sys.platform == "win32":
        assert not running_inside_WSL()
    # IDEA: Run the tests from WSL in the CI?
    # assert not running_inside_WSL()


def test_get_vscode_executable_path():
    code = get_vscode_executable_path()
    if vscode_installed():
        assert code is not None and Path(code).exists()
    else:
        assert code is None


@xfails_on_windows(raises=UnsupportedPlatformError, reason="Uses RemoteV2", strict=True)
@test_requires_vscode
@requires_ssh_to_localhost
def test_sync_vscode_extensions_in_parallel_with_hostnames(
    monkeypatch: pytest.MonkeyPatch,
):
    import milatools.utils.vscode_utils

    # Make it so we use the local `code` executable instead of `code-server`.
    monkeypatch.setattr(
        milatools.utils.vscode_utils,
        find_code_server_executable.__name__,
        Mock(
            spec=find_code_server_executable, return_value=get_vscode_executable_path()
        ),
    )
    sync_vscode_extensions_in_parallel_with_hostnames(
        # Make the destination slightly different so it actually gets wrapped as a
        # `Remote(v2)` object.
        "localhost",
        destinations=[f"{getpass.getuser()}@localhost"],
    )


@test_requires_vscode
@requires_ssh_to_localhost
def test_sync_vscode_extensions_in_parallel():
    results = sync_vscode_extensions_in_parallel(Local(), dest_clusters=[Local()])
    assert results == {"localhost": {"info": "Done.", "progress": 0, "total": 0}}


@test_requires_vscode
def test_install_vscode_extensions_task_function():
    with multiprocessing.Manager() as manager:
        from milatools.utils.parallel_progress import TaskID

        task_progress_dict: DictProxy[TaskID, ProgressDict] = manager.dict()
        result = install_vscode_extensions_task_function(
            task_progress_dict=task_progress_dict,
            task_id=TaskID(0),
            dest_hostname="localhost",
            source_extensions=get_local_vscode_extensions(),
            remote=Local(),
            source_name="localhost",
        )
        assert result == {"info": "Done.", "progress": 0, "total": 0}
        assert task_progress_dict[TaskID(0)] == result


def test_install_vscode_extension():
    raise NotImplementedError("TODO")


def test_get_local_vscode_extensions():
    raise NotImplementedError("TODO")


def test_get_remote_vscode_extensions():
    raise NotImplementedError("TODO")


def test_extensions_to_install():
    raise NotImplementedError("TODO")


def test_find_code_server_executable():
    raise NotImplementedError("TODO")
