from __future__ import annotations

import asyncio.subprocess
import contextlib
import dataclasses
import re
import shlex
import subprocess
import sys

from milatools.cli import console
from milatools.cli.utils import (
    MilatoolsUserError,
    get_hostname_to_use_for_compute_node,
    stripped_lines_of,
)
from milatools.utils.remote_v1 import Hide
from milatools.utils.remote_v2 import RemoteV2, logger, ssh_command
from milatools.utils.runner import Runner


class JobNotRunningError(RuntimeError):
    """Raised when trying to call `run` or `run_async` on a ComputeNode whose job has
    already been closed."""

    def __init__(self, job_id: int, *args: object) -> None:
        super().__init__(
            f"ComputeNode for job {job_id} has been closed and is unusable, since the "
            f"job has already ended!",
            *args,
        )
        self.job_id = job_id


@dataclasses.dataclass
class ComputeNode(Runner):
    """Runs commands on a compute node with `srun --jobid {job_id}` from the login node.

    This essentially runs this:
    `ssh {cluster} srun --overlap --jobid {job_id} {command}`
    in a subprocess each time `run` is called.

    NOTE: Found out about this trick from https://hpc.fau.de/faq/how-can-i-attach-to-a-running-slurm-job/
    """

    login_node: RemoteV2
    """The login node of the SLURM cluster."""

    job_id: int
    """The job ID of the job running on the compute node."""

    salloc_subprocess: asyncio.subprocess.Process | None = dataclasses.field(
        default=None, repr=False, compare=False
    )
    """A handle to the subprocess that is running the `salloc` command."""

    hostname: str = dataclasses.field(init=False)
    """Name of the compute node, as seen in `squeue`, `sacct` or `$SLURMD_NODENAME`."""

    _closed: bool = dataclasses.field(default=False, init=False, repr=False)

    def __post_init__(self):
        node_name = self.get_output("echo $SLURMD_NODENAME", display=False, hide=True)
        # We show the hostname of the compute node during commands, even though we're
        # actually running `ssh <login-node> srun --jobid <job-id> <command>`
        cluster = self.login_node.hostname
        self.hostname: str = get_hostname_to_use_for_compute_node(
            node_name,
            cluster=cluster,
            ssh_config_path=self.login_node.ssh_config_path,
        )

    @staticmethod
    async def connect(
        login_node: RemoteV2,
        job_id_or_node_name: int | str,
    ) -> ComputeNode:
        return await _connect_to_running_job(
            login_node=login_node, jobid_or_nodename=job_id_or_node_name
        )

    def run(
        self, command: str, display: bool = True, warn: bool = False, hide: Hide = False
    ):
        srun_command, input = self._get_srun_command_and_input(command, display=display)
        return self.login_node.run(
            command=srun_command,
            input=input,
            display=False,
            warn=warn,
            hide=hide,
        )

    async def run_async(
        self,
        command: str,
        display: bool = True,
        warn: bool = False,
        hide: Hide = False,
    ) -> subprocess.CompletedProcess[str]:
        srun_command, input = self._get_srun_command_and_input(command, display=display)
        return await self.login_node.run_async(
            command=srun_command,
            input=input,
            display=False,
            warn=warn,
            hide=hide,
        )

    def _get_srun_command_and_input(self, command: str, display: bool):
        """Common portion of `run` and `run_async`."""
        if self._closed:
            raise JobNotRunningError(self.job_id)
        if display:
            # Show the compute node hostname instead of the login node.
            console.log(f"({self.hostname}) $ {command}", style="green")

        if shlex.quote(command) == command:
            # The command is simple and doesn't need to be shell-escaped, so we can run
            # it directly with `ssh`.
            _command = command
            input = None
        else:
            # The command might have some shell syntax that needs to be preserved, so we run
            # `ssh (...) bash` and feed the input to the subprocess' stdin.
            _command = "bash"
            input = command + "\n"
        srun_command = (
            f"srun --ntasks=1 --overlap --quiet --jobid {self.job_id} {_command}"
        )
        return srun_command, input

    def __del__(self):
        if not self._closed and self.salloc_subprocess:
            try:
                self.salloc_subprocess.terminate()
                logger.warning(
                    f"Compute node is being deleted without having been closed!\n"
                    f"Terminated job {self.job_id} on {self.hostname}."
                )
            except ProcessLookupError:
                pass  # salloc subprocess had already been killed, all good.

    def __enter__(self):
        return self

    def __exit__(self, *excinfo):
        self.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *excinfo):
        await self.close_async()

    def close(self):
        """Ends the job.

        The ComputeNode becomes unusable.
        """
        if self._closed:
            logger.warning(f"Job {self.job_id} has already been closed.")
            return
        logger.info(f"Stopping job {self.job_id}.")
        if self.salloc_subprocess:
            self.salloc_subprocess.terminate()
        else:
            self.login_node.run(f"scancel {self.job_id}")
        self._closed = True

    async def close_async(self):
        """Cancels the running job using `scancel`."""
        if self._closed:
            logger.warning(f"Job {self.job_id} has already been closed.")
            return
        logger.info(f"Stopping job {self.job_id}.")
        if self.salloc_subprocess is not None:
            assert self.salloc_subprocess.stdin is not None
            # NOTE: This will exit cleanly because we don't have nested terminals or
            # job steps.
            logger.debug("Exiting the salloc subprocess gracefully.")
            await self.salloc_subprocess.communicate("exit\n".encode())  # noqa: UP012
        else:
            # The scancel below is done even though it's redundant, just to be safe.
            await self.login_node.run_async(
                f"scancel {self.job_id}",
                display=True,
                hide=False,
                warn=True,
            )
        self._closed = True


async def get_queued_milatools_job_ids(
    login_node: RemoteV2, job_name: str | None = "mila-code"
) -> set[int]:
    jobs = await login_node.get_output_async(
        "squeue --noheader --me --format=%A"
        + (f" --name={job_name}" if job_name is not None else "")
    )
    return set([int(job_id_str) for job_id_str in jobs.splitlines()])


@contextlib.asynccontextmanager
async def cancel_new_jobs_on_interrupt(login_node: RemoteV2, job_name: str):
    """ContextManager that handles interruptions while creating a new allocation.

    This handles the case where an interrupt is raised while running a command over SSH
    that creates a new job allocation (either salloc or sbatch) before we are able to
    parse the job id. (In the case where we have the job ID, we simply use
    `scancel {job_id}`).

    In this case, we try to cancel the new job(s) that have appeared since entering the
    `async with` block that have the name `job_name`. Emits a warning in the (unlikely)
    case where such jobs are not found, as it means that there could be a "zombie" job
    allocation on the cluster.
    """
    jobs_before = await get_queued_milatools_job_ids(login_node, job_name=job_name)
    if jobs_before:
        logger.info(
            f"Existing jobs on {login_node.hostname} with name {job_name}: {jobs_before}"
        )
    else:
        logger.debug(
            f"There are currently no jobs with name {job_name} on {login_node.hostname}."
        )
    try:
        yield
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.warning("Interrupted before we were able to parse a job id!")
        jobs_after = login_node.get_output(
            f"squeue --noheader --me --format=%A --name={job_name}"
        )
        jobs_after = list(map(int, stripped_lines_of(jobs_after)))
        # We were unable to get the job id, so we'll try to cancel only the newly
        # spawned jobs from this user that match the set name.
        new_jobs = list(set(jobs_after) - set(jobs_before))
        if len(new_jobs) == 1:
            job_id = new_jobs[0]
            logger.warning(
                f"Cancelling job {job_id} since it is the only new job of this "
                f"user with name {job_name!r} since the last call to salloc or sbatch.",
            )
            login_node.run(f"scancel {new_jobs[0]}", display=True, hide=False)
        elif len(new_jobs) > 1:
            logger.warning(
                f"There appears to be more than one new jobs from this user with "
                f"name {job_name!r} since the initial call to salloc or sbatch: "
                f"{new_jobs}\n"
                "Cancelling all of them to be safe...",
            )
            login_node.run(
                "scancel " + " ".join(str(job_id) for job_id in new_jobs),
                display=True,
                hide=False,
            )
        else:
            logger.warning(
                f"Unable to find any new job IDs with name {job_name!r} since the last "
                f"job allocation. This means that if job allocations were created, "
                "they might not have been properly cancelled. Please check that there "
                f"are no leftover pending jobs with the name {job_name!r} on the cluster!"
            )
        raise


async def salloc(
    login_node: RemoteV2, salloc_flags: list[str], job_name: str
) -> ComputeNode:
    """Runs `salloc` and returns a remote connected to the compute node."""
    # NOTE: Some SLURM clusters prevent submitting jobs from $HOME.
    salloc_command = "cd $SCRATCH && salloc " + shlex.join(salloc_flags)
    command = ssh_command(
        hostname=login_node.hostname,
        control_path=login_node.control_path,
        control_master="auto",
        control_persist="yes",
        command=salloc_command,
        ssh_config_path=login_node.ssh_config_path,
    )

    job_id: int | None = None

    # "Why not just use `subprocess.Popen`?", you might ask. Well, we're essentially
    # trying to go full-async so that the parsing of the job-id from stderr can
    # eventually be done at the same time as something else (while waiting for the
    # job to start) using things like `asyncio.gather` and `asyncio.wait_for`.
    async with cancel_new_jobs_on_interrupt(login_node, job_name):
        # NOTE: If stdin were not set to PIPE, then the terminal would actually be live
        # and run commands on the compute node! For instance if you were to do
        # `mila code .` and then write `salloc`, it would spawn a second job!
        logger.debug(f"(localhost) $ {shlex.join(command)}")
        console.log(
            f"({login_node.hostname}) $ {salloc_command}", style="green", markup=False
        )
        salloc_subprocess = await asyncio.subprocess.create_subprocess_exec(
            *command,
            shell=False,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert salloc_subprocess.stderr is not None
        # Getting an empty line only after salloc errors are done printing.
        while error_line := (await salloc_subprocess.stderr.readline()).decode():
            print(error_line, end="", file=sys.stderr)
            if job_id_match := re.findall(r"job allocation [0-9]+", error_line):
                job_id = int(job_id_match[0].split()[-1])
                break

    if job_id is None:
        raise RuntimeError("Unable to parse the job ID from the output of salloc!")

    try:
        console.log(f"Waiting for job {job_id} to start.", style="green")
        await wait_while_job_is_pending(login_node, job_id)
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.warning("Interrupted while waiting for the job to start.")
        login_node.run(f"scancel {job_id}", display=True, hide=False)
        logger.debug("Killing the salloc subprocess.")
        salloc_subprocess.terminate()
        raise

    # Note: While there are potentially states between `PENDING` and `RUNNING`, here
    # we're assuming that if the job is no longer pending, it's probably running. Even
    # if it isn't true, an informative error will most probably be given to the user by
    # the first `ssh <cluster> srun --job-id <job_id>` command.

    # NOTE: passing the process handle to this ComputeNodeRemote so it doesn't go out of
    # scope and die (which would kill the interactive job).
    return ComputeNode(
        job_id=job_id,
        login_node=login_node,
        salloc_subprocess=salloc_subprocess,
    )


async def sbatch(
    login_node: RemoteV2, sbatch_flags: list[str], job_name: str
) -> ComputeNode:
    """Runs `sbatch` and returns a remote connected to the compute node.

    The job script is actually the `sleep` command wrapped in an sbatch script thanks to
    [the '--wrap' argument of sbatch](https://slurm.schedmd.com/sbatch.html#OPT_wrap)

    This then waits asynchronously while the job show up as PENDING in the output of the
    `sacct` command.
    """
    # NOTE: cd to $SCRATCH because some SLURM clusters prevent submitting jobs from the
    # HOME directory. Also, if a cluster doesn't have $SCRACTCH set, we just stay in the
    # home directory, so no harm done.
    # Also, should we use --ntasks=1 --overlap in the wrapped `srun`, so that only one
    # task sleeps? Does that change anything?
    sbatch_command = (
        "cd $SCRATCH && sbatch --parsable "
        + shlex.join(sbatch_flags)
        + " --wrap 'srun sleep 7d'"
    )

    job_id = None
    async with cancel_new_jobs_on_interrupt(login_node, job_name):
        job_id = await login_node.get_output_async(
            sbatch_command, display=True, hide=False
        )
        job_id = int(job_id)

    try:
        await wait_while_job_is_pending(login_node, job_id)
    except (KeyboardInterrupt, asyncio.CancelledError):
        console.log(f"Received KeyboardInterrupt, cancelling job {job_id}")
        login_node.run(f"scancel {job_id}", display=True, hide=False)
        raise

    return ComputeNode(job_id=job_id, login_node=login_node)


async def _wait_while_job_is_in_state(login_node: RemoteV2, job_id: int, state: str):
    nodes: str | None = None
    current_state: str | None = None
    wait_time_seconds = 1
    attempt = 1

    while True:
        result = await login_node.run_async(
            f"sacct --jobs {job_id} --allocations --noheader --format=Node,State",
            display=False,
            warn=True,  # don't raise an error if the command fails.
            hide=True,
        )
        stdout = result.stdout.strip()
        nodes, _, current_state = stdout.rpartition(" ")
        nodes = nodes.strip()
        current_state = current_state.strip()
        logger.debug(f"{nodes=}, {current_state=}")

        if (
            result.returncode == 0
            and nodes
            and nodes != "None assigned"
            and current_state
            and current_state != state
        ):
            logger.info(
                f"Job {job_id} was allocated node(s) {nodes!r} and is in state "
                f"{current_state!r}."
            )
            return current_state

        waiting_until = f"Waiting {wait_time_seconds} seconds until job {job_id} "
        condition: str | None = None
        if result.returncode == 0 and not nodes and not current_state:
            condition = "shows up in the output of `sacct`."
        elif result.returncode != 0:
            # todo: Look into this case a bit more deeply. Seems like sometimes sacct
            # gives errors for example right after salloc, when the job id is not yet
            # in the slurm DB.
            condition = "shows up in the output of `sacct`."
        elif nodes == "None assigned":
            condition = "is allocated a node."
        elif current_state == state:
            condition = f"is no longer {state}."
        else:
            # TODO: Don't yet understand when this case could occur.
            logger.warning(
                f"Unexpected result from `sacct` for job {job_id}: {result.stdout=}, {result.stderr=}"
            )
            condition = "shows up correctly in the output of sacct."
        logger.info(waiting_until + condition)

        if attempt > 1:
            logger.debug(f"Attempt #{attempt}")

        await asyncio.sleep(wait_time_seconds)
        wait_time_seconds *= 2
        # wait at most 30 seconds for each attempt.
        wait_time_seconds = min(30, wait_time_seconds)
        attempt += 1


async def wait_while_job_is_pending(login_node: RemoteV2, job_id: int) -> str:
    """Waits until a job show up in `sacct` then waits until its state is not PENDING.

    Returns the `State` from `sacct` after the job is no longer pending.
    """
    return await _wait_while_job_is_in_state(login_node, job_id, state="PENDING")


async def _connect_to_running_job(
    jobid_or_nodename: int | str,
    login_node: RemoteV2,
) -> ComputeNode:
    # The `--job` flag used to be a string, might still be for some commands, so convert
    # an int string to int here just to be safe.
    if isinstance(jobid_or_nodename, str) and jobid_or_nodename.isdigit():
        jobid_or_nodename = int(jobid_or_nodename)

    if isinstance(jobid_or_nodename, int):
        job_id = jobid_or_nodename
        await wait_while_job_is_pending(login_node, job_id=job_id)
        return ComputeNode(login_node, job_id=job_id)

    node_name = jobid_or_nodename
    # we have to find the job id to use on the given node.
    jobs_on_node = await login_node.get_output_async(
        f"squeue --me --node {node_name} --noheader --format=%A"
    )
    jobs_on_node = [int(line) for line in stripped_lines_of(jobs_on_node)]

    if len(jobs_on_node) == 0:
        raise MilatoolsUserError(
            f"You don't appear to have any jobs currently running on node {node_name}. "
            "Please check again or specify the job id to connect to."
        )
    if len(jobs_on_node) > 1:
        raise MilatoolsUserError(
            f"You have more than one job running on node {node_name}: {jobs_on_node}.\n"
            "please use the `--job` flag to specify which job to connect to."
        )
    assert len(jobs_on_node) == 1
    return ComputeNode(login_node=login_node, job_id=jobs_on_node[0])
