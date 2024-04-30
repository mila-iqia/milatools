from __future__ import annotations

import contextlib
import functools
import shutil
import sys
import time
from collections.abc import Generator
from logging import getLogger as get_logger
from pathlib import Path
from unittest.mock import Mock

import paramiko.ssh_exception
import pytest
import questionary
from fabric.connection import Connection

from milatools.cli import console
from milatools.cli.init_command import DRAC_CLUSTERS, setup_ssh_config
from milatools.utils.remote_v1 import RemoteV1
from milatools.utils.remote_v2 import (
    RemoteV2,
    get_controlpath_for,
    is_already_logged_in,
)
from tests.integration.conftest import SLURM_CLUSTER

logger = get_logger(__name__)
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
    import milatools.utils.remote_v1

    monkeypatch.setattr(milatools.utils.remote_v1, Connection.__name__, MockConnection)
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
    return RemoteV1(hostname=mock_connection.host, connection=mock_connection)


@pytest.fixture(scope="function")
def login_node(cluster: str) -> RemoteV1 | RemoteV2:
    """Fixture that gives a Remote connected to the login node of a slurm cluster.

    NOTE: Making this a function-scoped fixture because the Connection object of the
    Remote seems to be passed (and reused?) when creating the `SlurmRemote` object.

    We want to avoid that, because `SlurmRemote` creates jobs when it runs commands.
    We also don't want to accidentally end up with `login_node` that runs commands on
    compute nodes because a previous test kept the same connection object while doing
    salloc (just in case that were to happen).
    """
    if cluster not in ["mila", "localhost"] and not is_already_logged_in(cluster):
        pytest.skip(
            f"Requires ssh access to the login node of the {cluster} cluster, and a "
            "prior connection to the cluster."
        )
    if sys.platform == "win32":
        return RemoteV1(cluster)
    return RemoteV2(cluster)


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

    # TODO: Re-enable this, but only on tests that say that they run jobs on the
    # cluster.
    # with cancel_all_milatools_jobs_before_and_after_tests(slurm_cluster_hostname):
    return slurm_cluster_hostname


@contextlib.contextmanager
def cancel_all_milatools_jobs_before_and_after_tests(login_node: RemoteV1 | RemoteV2):
    from .integration.conftest import WCKEY

    logger.info(
        f"Cancelling milatools test jobs on {cluster} before running integration tests."
    )
    login_node.run(f"scancel -u $USER --wckey={WCKEY}")
    time.sleep(1)
    # Note: need to recreate this because login_node is a function-scoped fixture.
    yield
    logger.info(
        f"Cancelling milatools test jobs on {cluster} after running integration tests."
    )
    login_node.run(f"scancel -u $USER --wckey={WCKEY}")
    time.sleep(1)
    # Display the output of squeue just to be sure that the jobs were cancelled.
    logger.info(f"Checking that all jobs have been cancelked on {cluster}...")
    login_node.run("squeue --me")


@pytest.fixture(
    scope="session", params=[True, False], ids=["already_logged_in", "not_logged_in"]
)
def already_logged_in(
    cluster: str,
    request: pytest.FixtureRequest,
    tmp_path_factory: pytest.TempPathFactory,
) -> Generator[bool, None, None]:
    # TODO: Make a fixture that goes through 2FA on all the DRAC clusters, then runs
    # all integration tests with and without existing connections already setup.
    # saves those controlpaths (by moving them?) into a temp directory of some sort,
    # then runs all the integration tests with "no initial connection", then runs them
    # again with the controlpaths at the right place again connections to the DRAC cluster, then, go through 2FA on the clusters
    should_already_be_logged_in_during_tests = request.param
    assert isinstance(should_already_be_logged_in_during_tests, bool)

    logged_in_before_tests = is_already_logged_in(cluster)
    control_path = get_controlpath_for(cluster)
    if logged_in_before_tests and should_already_be_logged_in_during_tests:
        # All good.
        console.log(f"Reusing an existing connection to {cluster} in tests.")
        yield True
        return

    if not logged_in_before_tests and not should_already_be_logged_in_during_tests:
        # All good.
        console.log(
            f"No prior connection to {cluster} before running tests, as desired."
        )
        yield False
        return

    if not logged_in_before_tests and should_already_be_logged_in_during_tests:
        console.log(
            f"No prior connection to {cluster} before running tests, creating one."
        )

        if cluster in DRAC_CLUSTERS:
            # todo: Seems like logger.warning is not being displayed in the test output
            # somehow?
            console.log(
                f"Going through 2FA with cluster {cluster} only once before tests."
            )

        RemoteV2(cluster)
        assert is_already_logged_in(cluster, also_run_command_to_check=True)
        yield True
        # TODO: Should we remove the connection after running the tests?
        return

    control_path = get_controlpath_for(cluster)
    assert control_path.exists()
    backup_dir = tmp_path_factory.mktemp("backup")

    console.log(
        f"Temporarily moving {control_path} to {backup_dir} so tests are run without "
        f"an existing connection."
    )
    moved_path = shutil.move(str(control_path), str(backup_dir))
    moved_path = Path(moved_path)
    try:
        yield False
    finally:
        console.log(f"Restoring the Control socket from {moved_path} to {control_path}")
        assert moved_path.exists()
        if control_path.exists():
            control_path.unlink()
        shutil.move(moved_path, control_path)


@pytest.fixture()
def ssh_config_file(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Fixture that creates the SSH config as setup by `mila init`."""
    from milatools.cli.init_command import yn

    # NOTE: might want to put this in a fixture if we wanted the "real" mila / drac
    # usernames in the config.
    mila_username = drac_username = "bob"

    ssh_config_path = tmp_path_factory.mktemp(".ssh") / "ssh_config"

    def _yn(question: str) -> bool:
        question = question.strip()
        known_questions = {
            f"There is no {ssh_config_path} file. Create one?": True,
            "Do you also have an account on the ComputeCanada/DRAC clusters?": True,
            "Is this OK?": True,
        }
        if question in known_questions:
            return known_questions[question]
        raise NotImplementedError(f"Unexpected question: {question}")

    mock_yn = Mock(spec=yn, side_effect=_yn)

    import milatools.cli.init_command

    monkeypatch.setattr(milatools.cli.init_command, yn.__name__, mock_yn)

    def _mock_unsafe_ask(question: str, *args, **kwargs) -> str:
        question = question.strip()
        known_questions = {
            "What's your username on the mila cluster?": mila_username,
            "What's your username on the CC/DRAC clusters?": drac_username,
        }
        if question in known_questions:
            return known_questions[question]
        raise NotImplementedError(f"Unexpected question: {question}")

    def _mock_text(message: str, *args, **kwargs):
        return Mock(
            spec=questionary.Question,
            unsafe_ask=Mock(
                spec=questionary.Question.unsafe_ask,
                side_effect=functools.partial(_mock_unsafe_ask, message),
            ),
        )

    mock_text = Mock(
        spec=questionary.text,
        side_effect=_mock_text,
    )
    monkeypatch.setattr(questionary, questionary.text.__name__, mock_text)

    setup_ssh_config(ssh_config_path)
    assert ssh_config_path.exists()
    return ssh_config_path
