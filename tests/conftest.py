from __future__ import annotations

import datetime
import functools
import os
import re
import socket
import sys
from collections.abc import Generator
from logging import getLogger as get_logger
from pathlib import Path
from unittest.mock import Mock

import pytest
import questionary
from fabric.connection import Connection

from milatools.cli.init_command import setup_ssh_config
from milatools.utils.remote_v1 import RemoteV1
from milatools.utils.remote_v2 import (
    RemoteV2,
    UnsupportedPlatformError,
    is_already_logged_in,
)

from .cli.common import (
    in_self_hosted_github_CI,
    passwordless_ssh_connection_to_localhost_is_setup,
    xfails_on_windows,
)
from .integration.conftest import (
    JOB_NAME,
    MAX_JOB_DURATION,
    SLURM_CLUSTER,
    WCKEY,
)

logger = get_logger(__name__)
unsupported_on_windows = xfails_on_windows(raises=UnsupportedPlatformError, strict=True)


pytest.register_assert_rewrite("tests.utils.runner_tests")


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


@pytest.fixture(scope="session")
def login_node_v2(cluster: str) -> RemoteV2:
    """Fixture that gives a Remote connected to the login node of a slurm cluster.

    This fixture is session-scoped, because RemoteV2 is pretty much stateless and can be
    safely reused.
    """
    if sys.platform == "win32":
        pytest.skip("Test uses RemoteV2.")
    if cluster not in ["mila", "localhost"] and not is_already_logged_in(cluster):
        pytest.skip(
            f"Requires ssh access to the login node of the {cluster} cluster, and a "
            "prior connection to the cluster."
        )
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

    cluster_name = request.param
    if not cluster_name:
        pytest.skip("Requires ssh access to a SLURM cluster.")

    clusters_in_maintenance = os.environ.get("CLUSTER_DOWN", "").split(",")
    if cluster_name in clusters_in_maintenance:
        pytest.skip(reason=f"Cluster {cluster_name} is down for maintenance.")
        # TODO: Seems not possible to add this marker to all tests?.
        # request.node.add_marker(
        #     pytest.mark.xfail(
        #         reason=f"Cluster {cluster_name} is down for maintenance.",
        # raises=subprocess.CalledProcessError,
        #     )
        # )
    return cluster_name


@pytest.fixture(scope="session")
def job_name(request: pytest.FixtureRequest) -> str | None:
    # TODO: Make the job name different based on the runner that is launching tests, so
    # that the `launches_job_fixture` doesn't scancel the test jobs launched from
    # another runner (e.g. me on my dev machine or laptop) on a cluster

    return getattr(request, "param", get_job_name_for_tests(request))


def get_job_name_for_tests(request: pytest.FixtureRequest) -> str | None:
    this_machine = socket.gethostname()
    this_test_name = request.node.name
    job_name = f"{JOB_NAME}_{this_test_name}_{this_machine}"
    if in_self_hosted_github_CI:
        # NOTE: We use this in the `build.yml` file to limit concurrent jobs for the
        # same branch/workflow
        # group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}
        # here we do something similar
        github_ref = os.environ["GITHUB_REF"]
        workflow_name = os.environ["GITHUB_WORKFLOW"]
        job_name += f"_{workflow_name}_{github_ref}"
    # remove anything weird like spaces, /, etc.
    job_name = re.sub(r"\W+", "-", job_name)
    return job_name


@functools.lru_cache
def get_slurm_account(cluster: str) -> str:
    """Gets the SLURM account of the user using sacctmgr on the slurm cluster.

    When there are multiple accounts, this selects the first account, alphabetically.

    On DRAC cluster, this uses the `def` allocations instead of `rrg`, and when
    the rest of the accounts are the same up to a '_cpu' or '_gpu' suffix, it uses
    '_cpu'.

    For example:

    ```text
    def-someprofessor_cpu  <-- this one is used.
    def-someprofessor_gpu
    rrg-someprofessor_cpu
    rrg-someprofessor_gpu
    ```
    """
    logger.info(
        f"Fetching the list of SLURM accounts available on the {cluster} cluster."
    )
    assert cluster in ["mila", "localhost"] or is_already_logged_in(cluster)
    result = RemoteV2(cluster).run(
        "sacctmgr --noheader show associations where user=$USER format=Account%50"
    )
    accounts = [line.strip() for line in result.stdout.splitlines()]
    assert accounts
    logger.info(f"Accounts on the slurm cluster {cluster}: {accounts}")
    account = sorted(accounts)[0]
    logger.info(f"Using account {account} to launch jobs in tests.")
    return account


@pytest.fixture(scope="session")
def slurm_account_on_cluster(cluster: str) -> str:
    if cluster not in ["mila", "localhost"] and not is_already_logged_in(cluster):
        # avoid test hanging on 2FA prompt.
        pytest.skip(reason=f"Test needs an existing connection to {cluster} to run.")
    return get_slurm_account(cluster)


@pytest.fixture(scope="session")
def max_job_duration(
    request: pytest.FixtureRequest, cluster: str
) -> datetime.timedelta:
    """Fixture that allows test to parametrize the duration of their jobs."""
    return getattr(request, "param", MAX_JOB_DURATION)


@pytest.fixture(scope="session")
def allocation_flags(
    request: pytest.FixtureRequest,
    slurm_account_on_cluster: str,
    job_name: str | None,
    max_job_duration: datetime.timedelta,
) -> list[str]:
    """Flags passed to salloc or sbatch during tests.

    When parametrized, overrides individual flags:
    ```python
    @pytest.mark.parametrize("allocation_flags", [{"some_flag": "some_value"}], indirect=True)
    def some_test(allocation_flags: list[str])
        assert "--some_flag=some_value" in allocation_flags
    ```
    """
    default_allocation_options = {
        "wckey": WCKEY,
        "account": slurm_account_on_cluster,
        "nodes": 1,
        "ntasks": 1,
        "cpus-per-task": 1,
        "mem": "1G",
        "time": max_job_duration,
        "oversubscribe": None,  # allow multiple such jobs to share resources.
    }
    if job_name is not None:
        # Only set the job name when needed. For example, `mila code` tests don't want
        # it to be set.
        default_allocation_options["job-name"] = job_name
    overrides = getattr(request, "param", {})
    assert isinstance(overrides, dict)
    if overrides:
        print(f"Overriding allocation options with {overrides}")
        default_allocation_options = default_allocation_options.copy()
        default_allocation_options.update(overrides)
    return [
        f"--{key}={value}" if value is not None else f"--{key}"
        for key, value in default_allocation_options.items()
    ]


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
