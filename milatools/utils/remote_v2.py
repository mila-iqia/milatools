from __future__ import annotations

import asyncio
import asyncio.subprocess
import getpass
import inspect
import itertools
import shlex
import shutil
import subprocess
import sys
from logging import getLogger as get_logger
from pathlib import Path
from typing import Literal

from paramiko import SSHConfig

from milatools.cli import console
from milatools.cli.remote import Hide
from milatools.cli.utils import (
    DRAC_CLUSTERS,
    MilatoolsUserError,
    get_fully_qualified_hostname_of_compute_node,
)

logger = get_logger(__name__)

SSH_CONFIG_FILE = Path.home() / ".ssh" / "config"
SSH_CACHE_DIR = Path.home() / ".cache" / "ssh"


class UnsupportedPlatformError(MilatoolsUserError):
    ...


def raise_error_if_running_on_windows():
    if sys.platform == "win32":
        raise UnsupportedPlatformError(
            "This feature isn't supported on Windows, as it requires an SSH client "
            "with SSH multiplexing support (ControlMaster, ControlPath and "
            "ControlPersist).\n"
            "Please consider switching to the Windows Subsystem for Linux (WSL).\n"
            "See https://learn.microsoft.com/en-us/windows/wsl/install for a guide on "
            "setting up WSL."
        )


def ssh_command(
    hostname: str,
    control_path: Path | Literal["none"],
    command: str,
    control_master: Literal["yes", "no", "auto", "ask", "autoask"] = "auto",
    control_persist: int | str | Literal["yes", "no"] = "yes",
    other_ssh_options: dict[str, str] | None = None,
):
    """Returns a tuple of strings to be used as the command to be run in a subprocess.

    Parameters
    ----------
    hostname: The hostname to connect to.
    control_path : See https://man.openbsd.org/ssh_config#ControlPath
    command: The command to run on the remote host (kept as a string).
    control_master: See https://man.openbsd.org/ssh_config#ControlMaster
    control_persist: See https://man.openbsd.org/ssh_config#ControlPersist

    Returns
    -------
    The tuple of strings to pass to `subprocess.run` or similar.
    """
    return (
        "ssh",
        f"-oControlMaster={control_master}",
        f"-oControlPersist={control_persist}",
        f"-oControlPath={control_path}",
        *(
            [f"-o{key}={value}" for key, value in other_ssh_options.items()]
            if other_ssh_options
            else []
        ),
        hostname,
        command,
    )


def control_socket_is_running(host: str, control_path: Path) -> bool:
    """Check whether the control socket at the given path is running."""
    if not control_path.exists():
        return False

    result = subprocess.run(
        (
            "ssh",
            "-O",
            "check",
            f"-oControlPath={control_path}",
            host,
        ),
        check=False,
        capture_output=True,
        text=True,
        shell=False,
    )
    if (
        result.returncode != 0
        or not result.stderr
        or not result.stderr.startswith("Master running")
    ):
        logger.debug(f"{control_path=} doesn't exist or isn't running: {result=}.")
        return False
    return True


async def control_socket_is_running_async(host: str, control_path: Path) -> bool:
    """Check whether the control socket at the given path is running asynchronously."""
    if not control_path.exists():
        return False

    result = await run(
        (
            "ssh",
            "-O",
            "check",
            f"-oControlPath={control_path}",
            host,
        ),
        warn=True,
        hide=True,
    )
    if (
        result.returncode != 0
        or not result.stderr
        or not result.stderr.startswith("Master running")
    ):
        logger.debug(f"{control_path=} doesn't exist or isn't running: {result=}.")
        return False
    return True


class RemoteV2:
    """Simpler Remote where commands are run in subprocesses sharing an SSH connection.

    This doesn't work on Windows, as it assumes that the SSH client has SSH multiplexing
    support (ControlMaster, ControlPath and ControlPersist).
    """

    def __init__(
        self,
        hostname: str,
        *,
        control_path: Path | None = None,
        ssh_options: dict[str, str] | None = None,
    ):
        """Create an SSH connection using this control_path, creating it if necessary.

        Parameters
        ----------
        hostname: The hostname to connect to.
        control_path: The path where the control socket will be created if it doesn't \
            already exist. You can use `get_controlpath_for` to get this for a given
            hostname.
        """
        self.hostname = hostname
        self.control_path = control_path or get_controlpath_for(hostname)
        self.ssh_options = ssh_options or {}

        if not control_socket_is_running(self.hostname, self.control_path):
            logger.info(
                f"Creating a reusable connection to the {self.hostname} cluster."
            )
            setup_connection_with_controlpath(
                self.hostname,
                self.control_path,
                timeout=None,
                display=False,
                other_ssh_options=ssh_options,
            )
        else:
            logger.info(f"Reusing an existing SSH socket at {self.control_path}.")

        assert control_socket_is_running(self.hostname, self.control_path)

    def run(
        self, command: str, display: bool = True, warn: bool = False, hide: Hide = False
    ):
        """Runs the given command on the remote and returns the result.

        This executes the command in an ssh subprocess, which, thanks to the
        ControlMaster/ControlPath/ControlPersist options, will reuse the existing
        connection to the remote.

        Parameters
        ----------
        command: The command to run.
        display: Display the command on the console before it is run.
        warn: If `true` and an exception occurs, warn instead of raising the exception.
        hide: Controls the printing of the subprocess' stdout and stderr.

        Returns
        -------
        A `subprocess.CompletedProcess` object with the output of the subprocess.
        """
        assert self.control_path.exists()
        run_command = ssh_command(
            hostname=self.hostname,
            control_path=self.control_path,
            control_master="auto",
            control_persist="yes",
            command=command,
            other_ssh_options=self.ssh_options,
        )
        logger.debug(f"(local) $ {shlex.join(run_command)}")
        if display:
            console.log(f"({self.hostname}) $ {command}", style="green")
        result = subprocess.run(
            run_command,
            capture_output=True,
            check=not warn,
            text=True,
            bufsize=1,  # 1 means line buffered
        )
        if result.stdout:
            if hide not in [True, "out", "stdout"]:
                print(result.stdout)
            logger.debug(f"{result.stdout}")
        if result.stderr:
            if hide not in [True, "err", "stderr"]:
                print(result.stderr)
            logger.debug(f"{result.stderr}")
        return result

    def get_output(
        self,
        command: str,
        display=False,
        warn=False,
        hide=True,
    ):
        """Runs the command and returns the stripped output as a string."""
        return self.run(command, display=display, warn=warn, hide=hide).stdout.strip()

    async def run_async(
        self, command: str, display: bool = True, warn: bool = False, hide: Hide = False
    ):
        """Runs the given command on the remote asynchronously and returns the result.

        This executes the command over ssh in an asyncio subprocess, which reuses the
        existing connection to the remote.

        Parameters
        ----------
        command: The command to run.
        display: Display the command on the console before it is run.
        warn: If `true` and an exception occurs, warn instead of raising the exception.
        hide: Controls the printing of the subprocess' stdout and stderr.

        Returns
        -------
        A `subprocess.CompletedProcess` object with the output of the subprocess.
        """
        assert self.control_path.exists()
        run_command = ssh_command(
            hostname=self.hostname,
            control_path=self.control_path,
            control_master="auto",
            control_persist="yes",
            command=command,
            other_ssh_options=self.ssh_options,
        )
        if display:
            console.log(f"({self.hostname}) $ {command}", style="green")
        result = await run(run_command, warn=warn, hide=hide)
        return result

    async def get_output_async(
        self,
        command: str,
        display=False,
        warn=False,
        hide=True,
    ):
        """Runs the command and returns the stripped output as a string."""
        return (
            await self.run_async(command, display=display, warn=warn, hide=hide)
        ).stdout.strip()

    def __repr__(self) -> str:
        params = ", ".join(
            f"{k}={repr(getattr(self, k))}"
            for k in inspect.signature(type(self)).parameters
        )
        return f"{type(self).__name__}({params})"


def salloc(login_node: RemoteV2, salloc_flags: list[str]) -> InteractiveRemote:
    """Runs `salloc` and returns a remote connected to the compute node."""
    salloc_command = "salloc " + shlex.join(salloc_flags)
    command = ssh_command(
        hostname=login_node.hostname,
        control_path=login_node.control_path,
        control_master="auto",
        control_persist="yes",
        other_ssh_options=login_node.ssh_options,
        command=salloc_command,
    )
    logger.debug(f"(local) $ {shlex.join(command)}")
    console.log(f"({login_node.hostname}) $ {salloc_command}", style="green")
    proc = subprocess.Popen(
        command,
        text=True,
        shell=False,
        bufsize=1,  # line buffered
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    # TODO: Waiting for the first line of output effectively waits until the job is
    # allocated, however maybe the "allocated job (...)" that is on stderr on the
    # Mila cluster could be printed in stdout on some other clusters.
    # TODO: Check this assumption holds for all clusters we care about, and if not,
    # devise a better way to wait until the job is running (perhaps using sacct as
    # done in `sbatch` below).
    # the allocation is done

    # Get the job id and the node hostname from the job.
    proc.stdin.write("echo $SLURM_JOB_ID,`hostname`\n")

    response = proc.stdout.readline().strip()

    # NOTE: the hostname of the compute node is actually fully-qualified here.
    job_id, _, compute_node_hostname = response.partition(",")
    job_id = int(job_id)
    logger.info(f"Was allocated node {compute_node_hostname} with job id {job_id}.")
    return InteractiveRemote(
        hostname=compute_node_hostname, job_id=job_id, salloc_subprocess=proc
    )


async def sbatch(login_node: RemoteV2, sbatch_flags: list[str]) -> PersistentRemote:
    """Runs `sbatch` and returns a remote connected to the compute node.

    The job script is actually the `sleep` command wrapped in an sbatch script thanks to
    [the '--wrap' argument of sbatch](https://slurm.schedmd.com/sbatch.html#OPT_wrap)

    This then waits asynchronously until the job show us as RUNNING in the output of the
    `sacct` command.
    """
    # idea: Find the job length from the sbatch flags if possible so we can do
    # --wrap='sleep {job_duration}' instead of 'sleep 7d' so the job doesn't look
    # like it failed or was interrupted, just cleanly exits before the end time.
    sbatch_command = shlex.join(
        ["sbatch", "--parsable"] + sbatch_flags + ["--wrap", "srun sleep 7d"]
    )
    job_id = await login_node.get_output_async(sbatch_command, display=True, hide=False)
    job_id = int(job_id)

    node_name, state = await get_node_of_job(login_node, job_id)
    # We need to get the hostname of the compute node that was allocated.
    # NOTE: in SlurmRemote it was extracted somehow. Here we'll query sacct for it
    # instead.

    # TODO: Add a job step in the wrapped command (if needed) that runs some interactive
    # terminal or something, then `sattach` to it on the login node over SSH, instead of
    # ssh-ing to the compute node. This way we're sure we land in the right job.
    fully_qualified_node_name = get_fully_qualified_hostname_of_compute_node(
        node_name, cluster=login_node.hostname
    )
    return PersistentRemote(hostname=fully_qualified_node_name, job_id=job_id)


async def get_node_of_job(login_node: RemoteV2, job_id: int) -> tuple[str, str]:
    """Waits until a job show up in `sacct` then waits until its state is not PENDING.

    Returns the `Node` and `State` from `sacct` after the job is no longer pending.
    """
    node: str | None = None
    state: str | None = None
    wait_time = 1
    for attempt in itertools.count(1):
        result = await login_node.run_async(
            f"sacct --jobs {job_id} --format=Node,State --allocations --noheader",
            warn=True,  # don't raise an error if the command fails.
            hide=True,
            display=False,
        )
        logger.debug(f"{result=}")
        stdout = result.stdout.strip()

        node, _, state = stdout.rpartition(" ")
        node = node.strip()

        logger.debug(f"{node=}, {state=}")

        if result.returncode != 0:
            logger.debug(f"Job {job_id} doesn't show up yet in the output of `sacct`.")
        elif node == "None assigned":
            logger.debug(
                f"Job {job_id} is in state {state!r} and has not yet been allocated a node."
            )
        elif state == "PENDING":
            logger.debug(f"Job {job_id} is still pending.")
        elif node and state:
            logger.info(
                f"Job {job_id} was allocated node {node!r} and is in state {state!r}."
            )
            break

        logger.info(
            f"Waiting {wait_time} seconds until job starts (attempt #{attempt}, {state=!r})"
        )
        await asyncio.sleep(wait_time)
        wait_time *= 2
        wait_time = min(30, wait_time)  # wait at most 30 seconds for each attempt.
    assert node is not None
    assert state is not None
    if state == "FAILED":
        logger.warning(RuntimeWarning(f"Seems like job {job_id} failed!"))
    return node, state


def option_dict_to_flags(options: dict[str, str]) -> list[str]:
    return [
        (
            f"--{key.removeprefix('--')}={value}"
            if value is not None
            else f"--{key.removeprefix('--')}"
        )
        for key, value in options.items()
    ]


class InteractiveRemote(RemoteV2):
    """A Remote connected to a compute node allocated with `salloc`."""

    def __init__(
        self,
        hostname: str,
        job_id: int,
        salloc_subprocess: subprocess.Popen,
        control_path: Path | None = None,
        ssh_options: dict[str, str] = {"StrictHostKeyChecking": "no"},
    ):
        # make a connection to the compute node through regular SSH
        # TODO: !! We don't know if we're landing in the right job !! (if there are
        # multiple of our jobs on the same node, we can't tell which one we'll land in!)
        # perhaps we should use an sbatch argument that prevents more than one of our
        # jobs to land on the same node if such an argument exists?
        super().__init__(
            hostname,
            control_path=control_path,
            ssh_options=ssh_options.copy(),
        )
        self.job_id = job_id
        self.salloc_subprocess = salloc_subprocess

    def run(
        self,
        command: str,
        display: bool = True,
        warn: bool = False,
        hide: Hide = False,
    ):
        # note: in order to get the slurm env variables, we should prepend `srun` to
        # the command.
        return super().run(command=command, display=display, warn=warn, hide=hide)

    def close(self) -> None:
        # note, could also do:
        # _out = self.get_output(f"scancel {self.job_id}")
        assert self.salloc_subprocess.stdin is not None
        _out, _err = self.salloc_subprocess.communicate("exit\n")
        self.salloc_subprocess.wait()


class PersistentRemote(RemoteV2):
    def __init__(
        self,
        hostname: str,
        job_id: int,
        *,
        control_path: Path | None = None,
        ssh_options: dict[str, str] | None = {"StrictHostKeyChecking": "no"},
    ):
        super().__init__(hostname, control_path=control_path, ssh_options=ssh_options)
        self.job_id = job_id


async def get_output(
    command: tuple[str, ...],
    warn: bool = False,
    hide: Hide = True,
):
    """Runs the command asynchronously in a subprocess and returns stripped output.

    The `hide` and `warn` parameters are the same as in `run`.
    """
    return (await run(command, warn=warn, hide=hide)).stdout.strip()


async def run(command: tuple[str, ...], warn: bool = False, hide: Hide = False):
    """Runs the command asynchronously in a subprocess and returns the result.

    Parameters
    ----------
    command: The command to run. (a tuple of strings, same as in subprocess.Popen).
    warn: When `True` and an exception occurs, warn instead of raising the exception.
    hide: Controls the printing of the subprocess' stdout and stderr.

    Returns
    -------
    The `subprocess.CompletedProcess` object with the result of the subprocess.

    Raises
    ------
    subprocess.CalledProcessError
        If an error occurs when running the command and `warn` is `False`.
    """
    logger.debug(f"(local) $ {shlex.join(command)}")
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    assert proc.returncode is not None
    logger.debug(f"[{command!r} exited with {proc.returncode}]")
    if proc.returncode != 0:
        if not warn:
            raise subprocess.CalledProcessError(
                returncode=proc.returncode,
                cmd=command,
                output=stdout,
                stderr=stderr,
            )
        if hide is not True:  # don't warn if hide is True.
            logger.warning(
                RuntimeWarning(
                    f"Command {command!r} returned non-zero exit code {proc.returncode}: {stderr}"
                )
            )
    result = subprocess.CompletedProcess(
        args=command,
        returncode=proc.returncode,
        stdout=stdout.decode(),
        stderr=stderr.decode(),
    )
    if result.stdout:
        if hide not in [True, "out", "stdout"]:
            print(result.stdout)
        logger.debug(f"{result.stdout}")
    if result.stderr:
        if hide not in [True, "err", "stderr"]:
            print(result.stderr)
        logger.debug(f"{result.stderr}")
    return result


def is_already_logged_in(cluster: str, also_run_command_to_check: bool = False) -> bool:
    """Checks whether we are already logged in to the given cluster.

    More specifically, this checks whether a reusable SSH control master is setup at the
    controlpath for the given cluster.

    NOTE: This function is not supported on Windows.

    Parameters
    ----------
    cluster: Hostname of the cluster to connect to.
    also_run_command_to_check: Whether we should also run a command over SSH to make
        100% sure that we are logged in. In most cases this isn't necessary so we can
        skip it, since it can take a few seconds.
    """
    control_path = get_controlpath_for(cluster)
    if not control_path.exists():
        logger.debug(f"ControlPath at {control_path} doesn't exist. Not logged in.")
        return False

    if not control_socket_is_running(cluster, control_path):
        return False
    if not also_run_command_to_check:
        return True
    return RemoteV2(cluster, control_path=control_path).get_output("echo OK") == "OK"


def get_controlpath_for(
    cluster: str,
    ssh_config_path: Path = SSH_CONFIG_FILE,
    ssh_cache_dir: Path | None = SSH_CACHE_DIR,
) -> Path:
    """Returns the control path to use for the given host using the ssh config.

    If the ControlPath option is set or applies to that host in the ssh config, returns
    the string with user, hostname, port already resolved (based on the values in the
    config).

    If the `ControlPath` option doesn't apply for that host in the SSH config
    and `ssh_cache_dir` is set, a path of the form
    '{ssh_cache_dir}/{user}@{qualified_hostname}:{port}' is returned, with values based
    on the values in the SSH config for that host if present.

    If `ssh_cache_dir` is not set, and the `ControlPath` option doesn't apply for that
    hostname, a `RuntimeError` is raised.
    """
    if not ssh_config_path.exists():
        raise MilatoolsUserError(f"SSH config file doesn't exist at {ssh_config_path}.")

    ssh_config = SSHConfig.from_path(str(ssh_config_path))
    values = ssh_config.lookup(cluster)
    if not (control_path := values.get("controlpath")):
        if ssh_cache_dir is None:
            raise RuntimeError(
                f"ControlPath isn't set in the ssh config for {cluster}, and "
                "ssh_cache_dir isn't set."
            )
        logger.debug(
            f"ControlPath isn't set for host {cluster}. Falling back to the ssh cache "
            f"directory at {ssh_cache_dir}."
        )
        hostname = values.get("hostname", cluster)
        username = values.get("user", getpass.getuser())
        port = values.get("port", 22)
        control_path = ssh_cache_dir / f"{username}@{hostname}:{port}"
    return Path(control_path).expanduser()


def setup_connection_with_controlpath(
    cluster: str,
    control_path: Path,
    display: bool = True,
    timeout: int | None = None,
    other_ssh_options: dict[str, str] | None = None,
) -> None:
    """Setup (or test) an SSH connection to this cluster using this control path.

    This goes through the 2FA process for clusters where 2FA is enabled.

    Parameters
    ----------
    cluster: name of the cluster to connect to.
    control_path: Path to the control socket file.
    display: Whether to display the command being run.
    timeout: Timeout in seconds for the subprocess. Set to `None` for no timeout.

    Raises
    ------
    subprocess.TimeoutExpired
        If `timeout` was passed and the subprocess times out.
    subprocess.CalledProcessError
        If the subprocess call raised an error.
    RuntimeError
        If the control path doesn't exist after the first connection, or if we didn't
        receive the output we expected from running the command.
    """
    raise_error_if_running_on_windows()

    if not control_path.exists():
        control_path.parent.mkdir(parents=True, exist_ok=True)

    command = "echo OK"
    first_command_args = ssh_command(
        hostname=cluster,
        control_path=control_path,
        control_master="auto",
        control_persist="yes",
        command=command,
        other_ssh_options=other_ssh_options,
    )
    if cluster in DRAC_CLUSTERS:
        console.log(
            f"The {cluster} cluster may be using two-factor authentication. ",
            "If you enabled 2FA, please take out your phone now.",
            sep="\n",
            style="yellow",
        )
        if shutil.which("sshpass"):
            # console.log(
            #     f"If 2FA is enabled on {cluster}, you should now receive a push "
            #     "notification in the Duo app. Confirm it to continue."
            #     style="yellow",
            # )
            # Enter 1 with `sshpass` to go straight to the prompt on the phone.
            first_command_args = (
                "sshpass",
                "-P",
                "Duo two-factor login",
                "-p",
                "1",
                *first_command_args,
            )
        else:
            logger.debug(
                f"`sshpass` is not installed. If 2FA is setup on {cluster}, you might "
                "be asked to press 1 or enter a 2fa passcode."
            )
    else:
        # NOTE: Assuming that passwordless ssh is setup to the cluster, we could also
        # use the sshpass command above even if 2fa isn't setup. This doesn't seem to
        # change anything about the outputs.
        pass

    logger.info(f"Making the first connection to {cluster}...")
    logger.debug(f"(local) $ {first_command_args}")
    if display:
        console.log(f"({cluster}) $ {command}", style="green")
    try:
        first_connection_result = subprocess.run(
            first_command_args,
            shell=False,
            text=True,
            bufsize=1,  # line buffered
            timeout=timeout,
            capture_output=True,
            check=True,
        )
        first_connection_output = first_connection_result.stdout
    except subprocess.TimeoutExpired as err:
        console.log(
            f"Timeout while setting up a reusable SSH connection to cluster {cluster}!"
        )
        raise err
    except subprocess.CalledProcessError as err:
        console.log(
            f"Unable to setup a reusable SSH connection to cluster {cluster}!", err
        )
        raise err
    if "OK" not in first_connection_output:
        raise RuntimeError(
            f"Did not receive the expected output ('OK') from {cluster}: "
            f"{first_connection_output}"
        )
    if not control_path.exists():
        raise RuntimeError(
            f"Expected a socket file to be created at {control_path} after the first "
            f"connection!"
        )
    logger.info(
        f"Success: Shareable SSH Connection to {cluster} is setup at {control_path=}"
    )
