from __future__ import annotations

import contextlib
import datetime
import functools
import io
import os
import re
import socket
import sys
import textwrap
from collections.abc import Generator
from logging import getLogger as get_logger
from pathlib import Path
from unittest.mock import Mock

import pytest
import pytest_asyncio
import rich
from fabric.connection import Connection
from pytest_mock import MockerFixture

import milatools.cli.code
import milatools.cli.commands
import milatools.cli.init_command
import milatools.cli.utils
import milatools.utils.compute_node
import milatools.utils.disk_quota
import milatools.utils.local_v2
import milatools.utils.parallel_progress
import milatools.utils.remote_v2
from milatools.cli import console
from milatools.cli.init_command import (
    get_windows_home_path_in_wsl,
    setup_ssh_config,
)
from milatools.cli.utils import SSH_CONFIG_FILE, running_inside_WSL
from milatools.utils.compute_node import get_queued_milatools_job_ids
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
from .integration.conftest import JOB_NAME, MAX_JOB_DURATION, SLURM_CLUSTER, WCKEY
from .utils import test_parallel_progress


@pytest.fixture(autouse=True)
def use_wider_console_during_tests(monkeypatch: pytest.MonkeyPatch):
    """Make the console very wide so commands are not wrapped across multiple lines.

    This makes tests that check the output of commands easier to write. Also removes the
    path to the log and the log time from the console output.
    """
    regular_console = console
    test_console = rich.console.Console(
        record=True, width=200, log_time=False, log_path=False
    )

    monkeypatch.setattr(milatools.cli, "console", test_console)
    monkeypatch.setitem(globals(), "console", test_console)

    for module in [
        milatools.cli.commands,
        milatools.utils.compute_node,
        milatools.utils.local_v2,
        milatools.utils.parallel_progress,
        milatools.utils.remote_v2,
        milatools.utils.disk_quota,
        test_parallel_progress,
        milatools.cli.code,
    ]:
        # These modules import the console from milatools.cli before this runs, so we
        # need to patch them also.
        assert hasattr(module, "console")
        assert module.console is regular_console
        monkeypatch.setattr(module, "console", test_console)


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
    """Returns a Mock wrapping a real `Connection` instance.

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
    if cluster not in ["mila", "localhost"] and not is_already_logged_in(
        cluster, ssh_config_path=SSH_CONFIG_FILE
    ):
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
    if cluster not in ["mila", "localhost"] and not is_already_logged_in(
        cluster, ssh_config_path=SSH_CONFIG_FILE
    ):
        pytest.skip(
            f"Requires ssh access to the login node of the {cluster} cluster, and a "
            "prior connection to the cluster."
        )
    return RemoteV2(cluster)


@pytest.fixture(scope="session", params=[SLURM_CLUSTER] if SLURM_CLUSTER else [])
def cluster(request: pytest.FixtureRequest) -> str:
    """Fixture that gives the hostname of the slurm cluster to use for tests.

    NOTE: The `cluster` can also be parametrized indirectly by tests, for example:

    ```python
    @pytest.mark.parametrize("cluster", ["mila", "some_cluster"], indirect=True)
    def test_something(remote: Remote):
        ...  # here the remote is connected to the cluster specified above!
    ```
    """

    cluster_name = getattr(request, "param", None)
    if not cluster_name:
        pytest.skip("Requires ssh access to a SLURM cluster.")

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


@pytest_asyncio.fixture(scope="session")
async def launches_job_fixture(login_node_v2: RemoteV2, job_name: str):
    jobs_before = await get_queued_milatools_job_ids(login_node_v2, job_name=job_name)
    if jobs_before:
        logger.debug(f"Jobs in squeue before tests: {jobs_before}")
    try:
        yield
    finally:
        jobs_after = await get_queued_milatools_job_ids(
            login_node_v2, job_name=job_name
        )
        if jobs_before:
            logger.debug(f"Jobs after tests: {jobs_before}")

        new_jobs = jobs_after - jobs_before
        if new_jobs:
            console.log(f"Cancelling jobs {new_jobs} after running tests...")
            login_node_v2.run(
                "scancel " + " ".join(str(job_id) for job_id in new_jobs), display=True
            )
        else:
            logger.debug("Test apparently didn't launch any new jobs.")


launches_jobs = pytest.mark.usefixtures(launches_job_fixture.__name__)


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
    assert cluster in ["mila", "localhost"] or is_already_logged_in(
        cluster, ssh_config_path=SSH_CONFIG_FILE
    )
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
    if cluster not in ["mila", "localhost"] and not is_already_logged_in(
        cluster, ssh_config_path=SSH_CONFIG_FILE
    ):
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
def mila_username(request: pytest.FixtureRequest) -> str | None:
    return getattr(request, "param", "testuser_mila")


@pytest.fixture()
def drac_username(request: pytest.FixtureRequest) -> str | None:
    return getattr(request, "param", "testuser_drac")


@pytest.fixture()
def initial_contents(request: pytest.FixtureRequest) -> str | None:
    """Initial contents of the SSH config file.

    This is to be parametrized indirectly by tests, so that the `ssh_config_file` fixture
    is created using these initial contents.
    """
    return getattr(request, "param", None)


# @pytest.mark.parametrize("drac_username", [None, "testuser_drac"], indirect=True)
@pytest.fixture(scope="module")
def ssh_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Fixture that creates a temporary .ssh directory for testing."""
    ssh_dir = tmp_path_factory.mktemp(".ssh")
    return ssh_dir


@pytest.fixture(scope="function")
def ssh_config_file(
    tmp_path_factory: pytest.TempPathFactory,
    initial_contents: str | None,
    mila_username: str | None,
    drac_username: str | None,
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Fixture that returns the SSH config file created by `mila init`, using the given
    parameters.

    Does not use an input pipe. Uses mocks instead.

    To change initial conditions or parameters, tests can indirectly parametrize the
    fixtures that are used to create this one. For example, to change the initial contents
    of the SSH config file, parametrize the `initial_contents` fixture indirectly.
    """
    fake_home = tmp_path_factory.mktemp("fakehome")
    ssh_dir = fake_home / ".ssh"
    ssh_config_path = ssh_dir / "config"
    if initial_contents:
        ssh_config_path.parent.mkdir(parents=True, exist_ok=True)
        ssh_config_path.write_text(textwrap.dedent(initial_contents) + "\n")
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.setattr(
        milatools.cli.init_command,
        "DEFAULT_DRAC_PUBKEY_PATH",
        ssh_dir / "id_rsa_drac.pub",
    )
    monkeypatch.setattr(
        milatools.cli.init_command,
        "DEFAULT_MILA_PUBKEY_PATH",
        ssh_dir / "id_rsa_mila.pub",
    )

    known_questions_to_answers = {
        "account on the Mila cluster": mila_username is not None,
        "username on the Mila cluster": mila_username,
        "account on the DRAC/ComputeCanada clusters": drac_username is not None,
        "username on the DRAC/ComputeCanada clusters": drac_username,
        "Is this OK?": True,
    }

    # This is a placeholder; in a real implementation, this would return the actual account name.
    def mocked_confirm(question: str, *args, **kwargs) -> bool:
        """Mocked prompt to return predefined answers for known questions."""
        for known_question, answer in known_questions_to_answers.items():
            if known_question in question:
                return answer
        raise ValueError(f"Unexpected question: {question}")

    # This is a placeholder; in a real implementation, this would return the actual account name.
    def mocked_ask(question: str, *args, **kwargs) -> bool:
        """Mocked prompt to return predefined answers for known questions."""
        for known_question, answer in known_questions_to_answers.items():
            if known_question in question:
                return answer
        raise ValueError(f"Unexpected question: {question}")

    _mock_confirm = mocker.patch("rich.prompt.Confirm.ask", side_effect=mocked_confirm)
    _mock_ask = mocker.patch("rich.prompt.Prompt.ask", side_effect=mocked_ask)

    with (
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        setup_ssh_config(ssh_config_path)
    mocker.stopall()
    return ssh_config_path


@pytest.fixture
def pretend_to_be_in_WSL(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
):
    # By default, pretend to be in WSL. Indirect parametrization can be used to
    # overwrite this value for a given test (as is done below).
    in_wsl = getattr(request, "param", True)
    _mock_running_inside_WSL = Mock(spec=running_inside_WSL, return_value=in_wsl)
    monkeypatch.setattr(
        milatools.cli.utils,  # defined here
        running_inside_WSL.__name__,  # type: ignore
        _mock_running_inside_WSL,
    )
    # Unfortunately we have to also patch this everywhere we import it in other modules.
    for place_that_imports_it in [
        milatools.cli.code,
        milatools.cli.init_command,
    ]:
        monkeypatch.setattr(
            place_that_imports_it,
            running_inside_WSL.__name__,  # type: ignore
            _mock_running_inside_WSL,
        )

    return in_wsl


@pytest.fixture
def windows_home(pretend_to_be_in_WSL, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    windows_home = tmp_path / "fake_windows_home"
    windows_home.mkdir(exist_ok=False)
    monkeypatch.setattr(
        milatools.cli.init_command,
        get_windows_home_path_in_wsl.__name__,  # type: ignore
        Mock(spec=get_windows_home_path_in_wsl, return_value=windows_home),
    )
    return windows_home
