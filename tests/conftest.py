from __future__ import annotations

import os
from collections.abc import Generator
from unittest.mock import Mock

import paramiko.ssh_exception
import pytest
from fabric.connection import Connection

from milatools.cli.remote import Remote
from milatools.cli.utils import cluster_to_connect_kwargs

from .cli.common import in_github_CI

SLURM_CLUSTER = os.environ.get("SLURM_CLUSTER", "mila" if not in_github_CI else None)
"""The name of the slurm cluster to use for tests.

When running the tests on a dev machine, this defaults to the Mila cluster. Set to
`None` when running on the github CI.
"""


passwordless_ssh_connection_to_localhost_is_setup = False

try:
    Connection("localhost").open()
except (
    paramiko.ssh_exception.SSHException,
    paramiko.ssh_exception.NoValidConnectionsError,
):
    pass
else:
    passwordless_ssh_connection_to_localhost_is_setup = True


@pytest.fixture(
    scope="session",
    params=[
        pytest.param(
            "localhost",
            marks=pytest.mark.skipif(
                not passwordless_ssh_connection_to_localhost_is_setup,
                reason="Passwordless ssh access to localhost needs to be setup.",
            ),
        ),
        # TODO: Think about a smart way to enable this. Some tests won't work as-is.
        # pytest.param(
        #     "mila",
        #     marks=pytest.mark.skipif(
        #         "-vvv" not in sys.argv, reason="Not testing using the Mila cluster."
        #     ),
        # ),
    ],
)
def host(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture(scope="session")
def connection(host: str) -> Generator[Connection, None, None]:
    """Fixture that gives a Connection object that is reused by all tests."""
    with Connection(host) as connection:
        yield connection


@pytest.fixture(scope="function")
def MockConnection(
    monkeypatch: pytest.MonkeyPatch, connection: Connection, host: str
) -> Mock:
    """Returns a Mock wrapping the `fabric.connection.Connection` class,.

    This is useful for tests that create a Remote without passing a connection, to make
    sure that any `Connection` instance created during tests is using our mock
    connection to `localhost` when possible.
    """
    # The return value of the constructor will always be the shared `Connection` object.
    MockConnection = Mock(
        name="MockConnection",
        wraps=Connection,
        return_value=Mock(
            name="mock_connection",
            # Modify the repr so they show up nicely in the regression files and with
            # consistent/reproducible names.
            wraps=connection,
            host=host,
            __repr__=lambda _: f"Connection({repr(host)})",
        ),
    )
    import milatools.cli.remote

    monkeypatch.setattr(milatools.cli.remote, Connection.__name__, MockConnection)
    return MockConnection


@pytest.fixture(scope="function")
def mock_connection(
    MockConnection: Mock,
) -> Mock:
    """returns a Mock wrapping a real `Connection` instance.

    This Mock is used to check how the connection is used by `Remote` and `SlurmRemote`.
    """
    mock_connection: Mock = MockConnection.return_value
    return mock_connection


@pytest.fixture(scope="function")
def remote(mock_connection: Connection):
    assert isinstance(mock_connection.host, str)
    return Remote(hostname=mock_connection.host, connection=mock_connection)


@pytest.fixture()
def login_node(cluster: str) -> Remote:
    """Fixture that gives a Remote connected to the login node of the slurm cluster.

    NOTE: Making this a function-scoped fixture because the Connection object is of the
    Remote is used when creating the SlurmRemotes.
    """

    return Remote(
        cluster,
        connection=Connection(
            cluster, connect_kwargs=cluster_to_connect_kwargs.get(cluster)
        ),
    )


@pytest.fixture(scope="session", params=[SLURM_CLUSTER])
def cluster(request: pytest.FixtureRequest) -> str:
    """Fixture that gives the hostname of the slurm cluster to use for tests.

    NOTE: The `cluster` can also be parametrized indirectly by tests, for example:

    ```python
    @pytest.mark.parametrize("cluster", ["mila", "some_cluster"], indirect=True)
    def test_something(remote: Remote):
        ...  # here the remote is connected to the cluster specified above!
    ```
    """
    slurm_cluster_hostname = request.param

    if not slurm_cluster_hostname:
        pytest.skip("Requires ssh access to a SLURM cluster.")
    return slurm_cluster_hostname
