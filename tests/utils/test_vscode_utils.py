from __future__ import annotations

import functools
import shutil
import sys
from logging import getLogger as get_logger
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio

from milatools.cli.utils import MilatoolsUserError, running_inside_WSL
from milatools.utils import vscode_utils
from milatools.utils.local_v2 import LocalV2
from milatools.utils.parallel_progress import (
    ProgressDict,
    report_progress,
)
from milatools.utils.remote_v1 import RemoteV1
from milatools.utils.remote_v2 import RemoteV2
from milatools.utils.vscode_utils import (
    _extensions_to_install,
    _find_code_server_executable,
    _get_local_vscode_executable_path,
    _get_vscode_extensions,
    _get_vscode_extensions_dict,
    _install_vscode_extension,
    _install_vscode_extensions_task_function,
    get_code_command,
    get_expected_vscode_settings_json_path,
    sync_vscode_extensions,
    vscode_installed,
)

from ..cli.common import (
    in_github_CI,
    in_self_hosted_github_CI,
    requires_ssh_to_localhost,
    skip_if_on_github_cloud_CI,
)
from .test_remote_v2 import uses_remote_v2

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
    if vscode_installed():
        code = _get_local_vscode_executable_path()
        assert Path(code).exists()
    else:
        with pytest.raises(
            MilatoolsUserError, match="Command 'code' does not exist locally."
        ):
            _get_local_vscode_executable_path()


@pytest.fixture
def mock_find_code_server_executable(monkeypatch: pytest.MonkeyPatch):
    """Makes it so we use the local `code` executable instead of `code-server`.

    This makes it possible to treat the `code` executable on localhost just like the
    `code-server` executable on remote machines because they have mostly the same CLI.
    """
    import milatools.utils.vscode_utils

    mock_find_code_server_executable = AsyncMock(
        spec=_find_code_server_executable,
        return_value=_get_local_vscode_executable_path(),
    )
    monkeypatch.setattr(
        milatools.utils.vscode_utils,
        _find_code_server_executable.__name__,
        mock_find_code_server_executable,
    )
    return mock_find_code_server_executable


@uses_remote_v2
@requires_vscode
@requires_ssh_to_localhost
@pytest.mark.asyncio
async def test_sync_vscode_extensions(
    mock_find_code_server_executable: Mock, monkeypatch: pytest.MonkeyPatch
):
    # Skip the check that removes the source from the destinations.
    monkeypatch.setattr(
        vscode_utils,
        vscode_utils._remove_source_from_destinations.__name__,
        lambda source, destinations: destinations,
    )

    remote = await RemoteV2.connect("localhost")
    results = await sync_vscode_extensions(
        remote,
        # Make the destination slightly different to avoid the duplicate hostname
        # detection that happens in `sync_vscode_extensions`.
        destinations=[remote],
    )
    assert results == {"localhost": []}
    mock_find_code_server_executable.assert_called()


@pytest_asyncio.fixture
async def vscode_extensions(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Returns a dict of vscode extension names and versions to be installed locally.

    Here we pretend like some local vscode extensions are missing by patching the
    function that returns the local extensions to return only part of its actual result.
    """
    all_extensions = await _get_vscode_extensions(LocalV2())

    installed_extensions = all_extensions.copy()
    num_missing_extensions = 3
    missing_extensions = dict(
        installed_extensions.popitem() for _ in range(num_missing_extensions)
    )
    assert len(installed_extensions) + len(missing_extensions) == len(all_extensions)

    import milatools.utils.vscode_utils

    # `localhost` is the source, so it has all the extensions
    # the "remote" (just to localhost during tests) is missing some extensions
    mock_remote_extensions = AsyncMock(
        spec=_get_vscode_extensions_dict,
        return_value=(installed_extensions, str(_get_local_vscode_executable_path())),
    )
    monkeypatch.setattr(
        milatools.utils.vscode_utils,
        _get_vscode_extensions_dict.__name__,
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
@pytest.mark.asyncio
async def test_install_vscode_extensions_task_function(
    installed_extensions: dict[str, str],
    missing_extensions: dict[str, str],
    mock_find_code_server_executable: Mock,
):
    from milatools.utils.parallel_progress import TaskID

    logger.debug(f"{len(installed_extensions)=}, {len(missing_extensions)=}")
    # Pretend like we don't already have these extensions locally.

    task_progress_dict: dict[TaskID, ProgressDict] = {}
    _fake_remote = await RemoteV2.connect("localhost")
    result = await _install_vscode_extensions_task_function(
        report_progress=functools.partial(
            report_progress,
            progress_dict=task_progress_dict,
            task_id=TaskID(0),
        ),
        dest_hostname="fake_cluster",
        source_extensions=missing_extensions,
        remote=_fake_remote,
        source_name="localhost",
    )
    mock_find_code_server_executable.assert_called_once_with(
        _fake_remote, remote_vscode_server_dir="~/.vscode-server"
    )

    assert result == [
        f"{ext_name}@{ext_version}"
        for ext_name, ext_version in missing_extensions.items()
    ]
    assert task_progress_dict[TaskID(0)] == {
        "info": "Done.",
        "progress": len(missing_extensions),
        "total": len(missing_extensions),
    }


@uses_remote_v2
@requires_ssh_to_localhost
@requires_vscode
@pytest.mark.asyncio
async def test_install_vscode_extension(missing_extensions: dict[str, str]):
    extension_name, version = next(iter(missing_extensions.items()))
    result = await _install_vscode_extension(
        remote=(await RemoteV2.connect("localhost")),
        code_server_executable=str(_get_local_vscode_executable_path()),
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
@pytest.mark.asyncio
async def test_get_local_vscode_extensions():
    local_extensions = await _get_vscode_extensions(LocalV2())

    assert local_extensions and all(
        isinstance(ext, str) and isinstance(version, str)
        for ext, version in local_extensions.items()
    )


@uses_remote_v2
@requires_ssh_to_localhost
@requires_vscode
@pytest.mark.asyncio
async def test_get_remote_vscode_extensions(mock_find_code_server_executable):
    # We make it so this calls the local `code` command over SSH to localhost,
    # therefore the "remote" extensions are the same as the local extensions.
    fake_remote = await RemoteV2.connect("localhost")

    local_vscode_executable = _get_local_vscode_executable_path()
    assert local_vscode_executable is not None

    fake_remote_extensions = await _get_vscode_extensions_dict(
        fake_remote, code_server_executable=local_vscode_executable
    )
    assert fake_remote_extensions == await _get_vscode_extensions(LocalV2())


@requires_vscode
def test_extensions_to_install(
    all_extensions: dict[str, str],
    installed_extensions: dict[str, str],
    missing_extensions: dict[str, str],
):
    to_install = _extensions_to_install(
        source_extensions=all_extensions,
        dest_extensions=installed_extensions,
        source_name="foo",
        dest_name="bar",
    )
    assert to_install == missing_extensions


# TODO: This test assumes that the `code-server` executable already exists for you (the
# dev) on the slurm cluster used in tests.. This is not ideal!
# Perhaps we could remove `vscode-server` from one of the clusters before running the
# tests? However this sounds a bit dangerous.
@pytest.mark.slow
@skip_if_on_github_cloud_CI
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("remote_vscode_server_dir", "should_exist"),
    [
        (
            "~/.vscode-server",
            True,  # todo: Replace this hard-coded value with something smarter.
        ),
        ("~/.vscode-server-dir-that-doesnt-exist", False),
    ],
)
async def test_find_code_server_executable(
    login_node_v2: RemoteV2, remote_vscode_server_dir: str, should_exist: bool
):
    # NOTE: The `find` command in $HOME takes a very long time to run!
    code_server_exe_path = await _find_code_server_executable(
        login_node_v2,
        remote_vscode_server_dir=remote_vscode_server_dir,
    )
    if not should_exist:
        assert code_server_exe_path is None
    else:
        assert code_server_exe_path
        remote_home = await login_node_v2.get_output_async("echo $HOME")
        expected_dir = remote_vscode_server_dir.replace("~", remote_home)
        assert code_server_exe_path.startswith(expected_dir)
