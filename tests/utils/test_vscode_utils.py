from __future__ import annotations

import getpass
import multiprocessing
import shutil
import sys
from logging import getLogger as get_logger
from multiprocessing.managers import DictProxy
from pathlib import Path
from unittest.mock import Mock

import pytest

from milatools.cli.utils import running_inside_WSL
from milatools.utils.local_v1 import LocalV1
from milatools.utils.parallel_progress import ProgressDict
from milatools.utils.remote_v1 import RemoteV1
from milatools.utils.remote_v2 import RemoteV2, UnsupportedPlatformError
from milatools.utils.vscode_utils import (
    extensions_to_install,
    find_code_server_executable,
    get_code_command,
    get_expected_vscode_settings_json_path,
    get_local_vscode_extensions,
    get_remote_vscode_extensions,
    get_vscode_executable_path,
    install_vscode_extension,
    install_vscode_extensions_task_function,
    sync_vscode_extensions,
    sync_vscode_extensions_with_hostnames,
    vscode_installed,
)
from tests.integration.conftest import skip_if_not_already_logged_in

from ..cli.common import (
    in_github_CI,
    in_self_hosted_github_CI,
    requires_ssh_to_localhost,
    skip_if_on_github_cloud_CI,
    xfails_on_windows,
)

logger = get_logger(__name__)


def test_vscode_installed():
    """Check that on a dev machine with VsCode installed, this function returns True."""
    installed = vscode_installed()
    if in_self_hosted_github_CI:
        assert installed
    elif in_github_CI:
        assert not installed
    else:
        assert installed


@pytest.mark.parametrize("custom_code_command", ["ls", "fake-code-doesnt-exist"])
def test_vscode_installed_with_env_var(
    custom_code_command: str, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("MILATOOLS_CODE_COMMAND", custom_code_command)
    assert get_code_command() == custom_code_command
    assert vscode_installed() == bool(shutil.which(custom_code_command))


requires_vscode = pytest.mark.xfail(
    not vscode_installed(),
    reason="This machine doesn't have VsCode installed.",
    strict=True,
)

uses_remote_v2 = xfails_on_windows(
    raises=UnsupportedPlatformError, reason="Uses RemoteV2", strict=True
)


@requires_vscode
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


@pytest.fixture
def mock_find_code_server_executable(monkeypatch: pytest.MonkeyPatch):
    """Makes it so we use the local `code` executable instead of `code-server`."""
    import milatools.utils.vscode_utils

    mock_find_code_server_executable = Mock(
        spec=find_code_server_executable, return_value=get_vscode_executable_path()
    )
    monkeypatch.setattr(
        milatools.utils.vscode_utils,
        find_code_server_executable.__name__,
        mock_find_code_server_executable,
    )
    return mock_find_code_server_executable


@xfails_on_windows(raises=UnsupportedPlatformError, reason="Uses RemoteV2", strict=True)
@requires_vscode
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
    sync_vscode_extensions_with_hostnames(
        # Make the destination slightly different so it actually gets wrapped as a
        # `Remote(v2)` object.
        "localhost",
        destinations=[f"{getpass.getuser()}@localhost"],
    )


@requires_vscode
@requires_ssh_to_localhost
def test_sync_vscode_extensions_in_parallel():
    results = sync_vscode_extensions(LocalV1(), dest_clusters=[LocalV1()])
    assert results == {"localhost": {"info": "Done.", "progress": 0, "total": 0}}


@pytest.fixture
def vscode_extensions(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Returns a dict of vscode extension names and versions to be installed locally.

    Here we pretend like some local vscode extensions are missing by patching the
    function that returns the local extensions to return only part of its actual result.
    """
    all_extensions = get_local_vscode_extensions()

    installed_extensions = all_extensions.copy()
    num_missing_extensions = 3
    missing_extensions = dict(
        installed_extensions.popitem() for _ in range(num_missing_extensions)
    )
    assert len(installed_extensions) + len(missing_extensions) == len(all_extensions)

    import milatools.utils.vscode_utils

    # `localhost` is the source, so it has all the extensions
    # the "remote" (just to localhost during tests) is missing some extensions
    mock_remote_extensions = Mock(
        spec=get_remote_vscode_extensions,
        return_value=(installed_extensions, str(get_vscode_executable_path())),
    )
    monkeypatch.setattr(
        milatools.utils.vscode_utils,
        get_remote_vscode_extensions.__name__,
        mock_remote_extensions,
    )

    return all_extensions, installed_extensions, missing_extensions


@pytest.fixture
def all_extensions(
    vscode_extensions: tuple[dict[str, str], ...],
) -> dict[str, str]:
    """Dict of all the vscode extensions (on the source and the dest)."""
    return vscode_extensions[0]


@pytest.fixture
def installed_extensions(
    vscode_extensions: tuple[dict[str, str], ...],
) -> dict[str, str]:
    """Dict of the extensions that are already installed on the dest (missing some)."""

    return vscode_extensions[1]


@pytest.fixture
def missing_extensions(
    vscode_extensions: tuple[dict[str, str], ...],
) -> dict[str, str]:
    """Dict of the extensions that are missing from the destination."""
    return vscode_extensions[2]


def _remote(hostname: str):
    return RemoteV2(hostname) if sys.platform != "win32" else RemoteV1(hostname)


@uses_remote_v2
@requires_ssh_to_localhost
@requires_vscode
def test_install_vscode_extensions_task_function(
    installed_extensions: dict[str, str],
    missing_extensions: dict[str, str],
    mock_find_code_server_executable: Mock,
):
    with multiprocessing.Manager() as manager:
        from milatools.utils.parallel_progress import TaskID

        logger.debug(f"{len(installed_extensions)=}, {len(missing_extensions)=}")
        # Pretend like we don't already have these extensions locally.

        task_progress_dict: DictProxy[TaskID, ProgressDict] = manager.dict()

        _fake_remote = _remote("localhost")

        result = install_vscode_extensions_task_function(
            task_progress_dict=task_progress_dict,
            task_id=TaskID(0),
            dest_hostname="fake_cluster",
            source_extensions=missing_extensions,
            remote=_fake_remote,
            source_name="localhost",
        )
        mock_find_code_server_executable.assert_called_once_with(
            _fake_remote, remote_vscode_server_dir="~/.vscode-server"
        )

        assert result == {
            "info": "Done.",
            "progress": len(missing_extensions),
            "total": len(missing_extensions),
        }
        assert task_progress_dict[TaskID(0)] == result


@uses_remote_v2
@requires_ssh_to_localhost
@requires_vscode
def test_install_vscode_extension(missing_extensions: dict[str, str]):
    extension_name, version = next(iter(missing_extensions.items()))
    result = install_vscode_extension(
        remote=_remote("localhost"),
        code_server_executable=str(get_vscode_executable_path()),
        extension=f"{extension_name}@{version}",
        verbose=False,
    )
    assert result.returncode == 0
    assert (
        f"Extension '{extension_name}@{version}' is already installed." in result.stdout
    )
    assert not result.stderr


@requires_ssh_to_localhost
@requires_vscode
def test_get_local_vscode_extensions():
    local_extensions = get_local_vscode_extensions()
    assert local_extensions and all(
        isinstance(ext, str) and isinstance(version, str)
        for ext, version in local_extensions.items()
    )


@uses_remote_v2
@requires_ssh_to_localhost
@requires_vscode
def test_get_remote_vscode_extensions():
    # We make it so this calls the local `code` command over SSH to localhost,
    # therefore the "remote" extensions are the same as the local extensions.
    fake_remote = _remote("localhost")

    local_vscode_executable = get_vscode_executable_path()
    assert local_vscode_executable is not None

    fake_remote_extensions = get_remote_vscode_extensions(
        fake_remote, remote_code_server_executable=local_vscode_executable
    )
    # Because of the mocking we did above, this should be true
    assert fake_remote_extensions == get_local_vscode_extensions()


@requires_vscode
def test_extensions_to_install(
    all_extensions: dict[str, str],
    installed_extensions: dict[str, str],
    missing_extensions: dict[str, str],
):
    to_install = extensions_to_install(
        source_extensions=all_extensions,
        dest_extensions=installed_extensions,
        source_name="foo",
        dest_name="bar",
    )
    assert to_install == missing_extensions


@uses_remote_v2
@pytest.mark.parametrize(
    ("cluster", "remote_vscode_server_dir", "should_exist"),
    [
        pytest.param(
            "localhost",
            "~/vscode",
            False,
            marks=[
                skip_if_on_github_cloud_CI,
                requires_ssh_to_localhost,
                requires_vscode,
            ],
        ),
        pytest.param(
            "mila",
            "~/.vscode-server",
            True,
            marks=[
                skip_if_on_github_cloud_CI,
                skip_if_not_already_logged_in("mila"),
            ],
        ),
    ],
)
def test_find_code_server_executable(
    cluster: str, remote_vscode_server_dir: str, should_exist: bool
):
    code_server_exe_path = find_code_server_executable(
        RemoteV2(cluster), remote_vscode_server_dir=remote_vscode_server_dir
    )
    if not should_exist:
        assert code_server_exe_path is None
    else:
        assert code_server_exe_path and code_server_exe_path.startswith(
            code_server_exe_path
        )
