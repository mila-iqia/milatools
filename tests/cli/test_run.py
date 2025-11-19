import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from milatools.cli.run import run, run_cli
from milatools.utils.remote_v2 import RemoteV2


@pytest.mark.asyncio
async def test_run_command_logic():
    # Mock RemoteV2
    mock_remote1 = MagicMock(spec=RemoteV2)
    mock_remote1.hostname = "cluster1"
    mock_remote1.run_async = AsyncMock(
        return_value=subprocess.CompletedProcess(
            args="echo hello", returncode=0, stdout="hello\n", stderr=""
        )
    )

    mock_remote2 = MagicMock(spec=RemoteV2)
    mock_remote2.hostname = "cluster2"
    mock_remote2.run_async = AsyncMock(
        return_value=subprocess.CompletedProcess(
            args="echo hello", returncode=1, stdout="", stderr="error\n"
        )
    )

    clusters = [mock_remote1, mock_remote2]
    command = "echo hello"

    results = await run(command, clusters)

    assert len(results) == 2
    assert results[0].returncode == 0
    assert results[0].stdout == "hello\n"
    assert results[1].returncode == 1
    assert results[1].stderr == "error\n"


@pytest.mark.asyncio
async def test_run_cli_with_clusters():
    with (
        patch(
            "milatools.cli.run.RemoteV2.connect", new_callable=AsyncMock
        ) as mock_connect,
        patch("milatools.cli.run.run", new_callable=AsyncMock) as mock_run,
        patch("milatools.cli.run.console") as mock_console,
    ):
        mock_remote = MagicMock(spec=RemoteV2)
        mock_remote.hostname = "cluster1"
        mock_connect.return_value = mock_remote

        mock_run.return_value = [
            subprocess.CompletedProcess(
                args="echo hello", returncode=0, stdout="hello\n", stderr=""
            )
        ]

        await run_cli(["echo", "hello"], clusters="cluster1")

        mock_connect.assert_called_once_with("cluster1")
        mock_run.assert_called_once()
        assert mock_run.call_args[0][0] == "echo hello"
        assert mock_run.call_args[0][1] == [mock_remote]

        # Verify console output
        assert mock_console.print.call_count >= 2  # Initial message + output panel


@pytest.mark.asyncio
async def test_run_cli_default_clusters():
    with (
        patch("milatools.cli.run.RemoteV2") as MockRemoteV2,
        patch(
            "milatools.cli.run.control_socket_is_running_async", new_callable=AsyncMock
        ) as mock_check,
        patch("milatools.cli.run.run", new_callable=AsyncMock) as mock_run,
        patch("milatools.cli.run.console") as _mock_console,
    ):
        # Setup mocks
        mock_remote1 = MagicMock(spec=RemoteV2)
        mock_remote1.hostname = "mila"
        mock_remote1.control_path = "/tmp/mila_control"
        mock_remote1._start_async = AsyncMock()

        mock_remote2 = MagicMock(spec=RemoteV2)
        mock_remote2.hostname = "narval"
        mock_remote2.control_path = "/tmp/narval_control"

        # MockRemoteV2 constructor returns these mocks
        # We need to handle multiple calls to RemoteV2()
        # The code calls RemoteV2(name, _start_control_socket=False) for each default cluster

        # Let's simplify: assume only 2 default clusters for this test
        with patch("milatools.cli.run.DEFAULT_RUN_CLUSTERS", ["mila", "narval"]):
            MockRemoteV2.side_effect = [mock_remote1, mock_remote2]

            # mila is active, narval is not
            mock_check.side_effect = [True, False]

            mock_run.return_value = [
                subprocess.CompletedProcess(
                    args="echo hello", returncode=0, stdout="hello\n", stderr=""
                )
            ]

            await run_cli(["echo", "hello"])

            # Verify mila was started (connected)
            mock_remote1._start_async.assert_called_once()

            # Verify run was called only with mila
            mock_run.assert_called_once()
            assert len(mock_run.call_args[0][1]) == 1
            assert mock_run.call_args[0][1][0] == mock_remote1


if __name__ == "__main__":
    # Manual run if needed, but better to use pytest
    pass
