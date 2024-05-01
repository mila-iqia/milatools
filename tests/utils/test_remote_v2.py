from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
import pytest_asyncio

import milatools.utils.remote_v2
from milatools.utils.local_v2 import LocalV2
from milatools.utils.remote_v2 import (
    RemoteV2,
    UnsupportedPlatformError,
    control_socket_is_running,
    control_socket_is_running_async,
    get_controlpath_for,
    is_already_logged_in,
)

from ..cli.common import (
    requires_ssh_to_localhost,
    xfails_on_windows,
)
from .runner_tests import RunnerTests

uses_remote_v2 = xfails_on_windows(
    raises=UnsupportedPlatformError, reason="Uses RemoteV2", strict=False
)

pytestmark = [uses_remote_v2]


@pytest_asyncio.fixture
async def control_path_for_localhost(tmp_path: Path):
    """The `control_path` parameter of `RemoteV2` for connecting to localhost."""
    control_path = tmp_path / "socketfile"
    try:
        yield control_path
    finally:
        if control_path.exists():
            await LocalV2.run_async(
                (
                    "ssh",
                    f"-oControlPath={control_path}",
                    "-O",
                    "exit",
                    "localhost",
                ),
            )
            assert not control_path.exists()


@pytest.fixture
def mock_get_controlpath_for(
    monkeypatch: pytest.MonkeyPatch, control_path_for_localhost: Path
):
    """Mock of `get_controlpath_for` so it returns the control path we want to use."""
    mock_get_controlpath_for = Mock(
        wraps=get_controlpath_for, return_value=control_path_for_localhost
    )
    monkeypatch.setattr(
        milatools.utils.remote_v2,
        get_controlpath_for.__name__,
        mock_get_controlpath_for,
    )
    return mock_get_controlpath_for


@pytest.fixture(params=[True, False])
def already_logged_in_to_localhost(
    request: pytest.FixtureRequest,
    control_path_for_localhost: Path,
):
    """Fixture that makes the setup as if we are/aren't already logged in to `localhost`
    via SSH with a control socket."""
    expected_to_be_logged_in: bool = request.param
    assert not control_path_for_localhost.exists()

    if expected_to_be_logged_in:
        # manually setup the control socket to `localhost`.
        LocalV2.run(
            (
                "ssh",
                f"-oControlPath={control_path_for_localhost}",
                "-oControlMaster=auto",
                "-oControlPersist=60",
                "localhost",
            ),
        )
        assert control_path_for_localhost.exists()

    yield expected_to_be_logged_in


class TestRemoteV2(RunnerTests):
    """Tests for RemoteV2.

    The tests for the `run`/`run_async`/etc. methods are in the base class, we just
    supply the necessary fixtures here.
    """

    @pytest.fixture(scope="class")
    def runner(self, cluster: str):
        # Fixture that creates the runner used in the tests for run/run_async in the
        # base class.
        return RemoteV2(cluster)

    @requires_ssh_to_localhost
    @pytest.mark.parametrize("use_async_init", [False, True], ids=["sync", "async"])
    @pytest.mark.asyncio
    async def test_init_with_controlpath(
        self,
        control_path_for_localhost: Path,
        use_async_init: bool,
        ssh_config_file: Path,
    ):
        hostname = "localhost"
        remote = (
            (
                await RemoteV2.connect(
                    hostname,
                    control_path=control_path_for_localhost,
                    ssh_config_path=ssh_config_file,
                )
            )
            if use_async_init
            else RemoteV2(
                hostname,
                control_path=control_path_for_localhost,
                ssh_config_path=ssh_config_file,
            )
        )
        assert remote.control_path == control_path_for_localhost
        assert control_path_for_localhost.exists()

    @requires_ssh_to_localhost
    @pytest.mark.parametrize("use_async_init", [False, True], ids=["sync", "async"])
    @pytest.mark.asyncio
    async def test_init_with_none_controlpath(
        self,
        use_async_init: bool,
        control_path_for_localhost: Path,
        ssh_config_file: Path,
        mock_get_controlpath_for: Mock,
    ):
        """Checks that creating a `RemoteV2` with `control_path=None` calls
        `get_controlpath_for`."""
        hostname = "localhost"
        remote = (
            (
                await RemoteV2.connect(
                    hostname, control_path=None, ssh_config_path=ssh_config_file
                )
            )
            if use_async_init
            else RemoteV2(hostname, control_path=None, ssh_config_path=ssh_config_file)
        )
        mock_get_controlpath_for.assert_called_once()
        assert remote.control_path == control_path_for_localhost
        assert control_path_for_localhost.exists()


@requires_ssh_to_localhost
@pytest.mark.asyncio
async def test_is_already_logged_in(
    already_logged_in_to_localhost: bool,
    mock_get_controlpath_for: Mock,
):
    assert is_already_logged_in("localhost") == already_logged_in_to_localhost
    mock_get_controlpath_for.assert_called_once()


@requires_ssh_to_localhost
def test_controlsocket_is_running(
    already_logged_in_to_localhost: bool, control_path_for_localhost: Path
):
    assert (
        control_socket_is_running("localhost", control_path=control_path_for_localhost)
        == already_logged_in_to_localhost
    )


@requires_ssh_to_localhost
@pytest.mark.asyncio
async def test_controlsocket_is_running_async(
    already_logged_in_to_localhost: bool, control_path_for_localhost: Path
):
    assert (
        await control_socket_is_running_async(
            "localhost", control_path=control_path_for_localhost
        )
        == already_logged_in_to_localhost
    )
