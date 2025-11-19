from unittest.mock import Mock

import pytest
import pytest_asyncio

import milatools
import milatools.cli
import milatools.cli.run
from milatools.cli.run import get_cluster_remotes, run, run_cli
from milatools.utils.remote_v2 import RemoteV2


@pytest_asyncio.fixture(scope="session")
async def clusters():
    clusters = await get_cluster_remotes(None)
    if not clusters:
        pytest.skip("No clusters with active SSH connections found.")
    # assert False, clusters
    return clusters


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("echo hello", "hello"),
    ],
)
@pytest.mark.asyncio
async def test_run(command: str, expected: str, clusters: list[RemoteV2]):
    """Test that we get one output for each cluster with an active SSH connection."""
    assert clusters
    results = await run(command, clusters)
    assert all(expected in result.stdout for result in results)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("echo hello", "hello"),
    ],
)
@pytest.mark.asyncio
async def test_run_cli(
    command: str,
    expected: str,
    clusters: list[RemoteV2],
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
):
    """Test that we get one output for each cluster with an active SSH connection."""
    assert clusters
    monkeypatch.setattr(
        milatools.cli.run, "console", mock_console := Mock(wraps=milatools.cli.console)
    )

    await run_cli(command, "localhost")
    mock_console.print.assert_called()
    assert expected in capsys.readouterr()[0]
