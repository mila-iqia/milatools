from __future__ import annotations

import re
import shlex
import socket
import tempfile
import time
from collections.abc import Callable, Iterable, Sequence
from logging import getLogger as get_logger
from pathlib import Path, PurePosixPath
from queue import Empty, Queue
from typing import Literal, TextIO, overload

import fabric
import fabric.transfer
import invoke
import paramiko
import questionary as qn
from fabric import Connection
from typing_extensions import Self, TypedDict, deprecated

from ..cli.utils import (
    SSHConnectionError,
    T,
    cluster_to_connect_kwargs,
    control_file_var,
    here,
)

logger = get_logger(__name__)
batch_template = """#!/bin/bash
#SBATCH --output={output_file}
#SBATCH --ntasks=1

echo jobid = $SLURM_JOB_ID >> {control_file}

{command}
"""

Hide = Literal[True, False, "out", "stdout", "err", "stderr"]


class NodeNameDict(TypedDict):
    node_name: str


class NodeNameAndJobidDict(TypedDict):
    node_name: str
    jobid: str


class QueueIO:
    """A Queue used to store the output of the remote command."""

    # TODO: Why aren't we using something like `io.StringIO`?

    def __init__(self):
        self.q: Queue[str] = Queue()

    def write(self, s: str) -> None:
        self.q.put(s)

    def flush(self) -> None:
        pass

    def readlines(self, stop: Callable[[], bool]) -> Iterable[str]:
        """Read lines from the queue until empty and the stop condition is met."""
        current = ""  # the last line of text that was yielded.
        lines: Sequence[str] = tuple()
        while True:
            try:
                current += self.q.get(timeout=0.05)
                if "\n" in current:
                    *lines, current = current.split("\n")
                for line in lines:
                    yield f"{line}\n"
            except Empty:
                if stop():
                    if current:
                        yield current
                    return


def get_first_node_name(node_names_out: str) -> str:
    """Returns the name of the first node that was granted, given the string that salloc
    outputs to stdout.

    >>> get_first_node_name("cn-c001")
    'cn-c001'
    >>> get_first_node_name("cn-c[001-003]")
    'cn-c001'
    >>> get_first_node_name("cn-c[005,008]")
    'cn-c005'
    >>> get_first_node_name("cn-c001,rtx8")
    'cn-c001'
    """
    if "[" not in node_names_out:
        if "," in node_names_out:
            # different nodes
            return node_names_out.split(",")[0]
        # single node
        return node_names_out
    base, _, rest = node_names_out.partition("[")
    inside_brackets, _, _ = rest.partition("]")

    if "," in inside_brackets:
        return base + inside_brackets.split(",")[0]
    assert "-" in inside_brackets
    return base + inside_brackets.split("-")[0]


@deprecated("RemoteV1 is being deprecated. Use RemoteV2 instead.", category=None)
class RemoteV1:
    def __init__(
        self,
        hostname: str,
        connection: fabric.Connection | None = None,
        transforms: Sequence[Callable[[str], str]] = (),
        keepalive: int = 60,
        connect_kwargs: dict[str, str] | None = None,
    ):
        self.hostname = hostname
        try:
            if connection is None:
                _connect_kwargs = cluster_to_connect_kwargs.get(hostname)
                if connect_kwargs is not None:
                    _connect_kwargs = (_connect_kwargs or {}).copy()
                    _connect_kwargs.update(connect_kwargs)

                logger.info(f"Opening a new connection to {hostname}")
                logger.debug(f"connect_kwargs: {_connect_kwargs}")
                connection = Connection(hostname, connect_kwargs=_connect_kwargs)
                if keepalive:
                    connection.open()
                    # NOTE: this transport gets mocked in tests, so we use a "soft"
                    # typing override instead of an assertion check here.
                    # assert isinstance(connection.transport, paramiko.Transport)
                    transport: paramiko.Transport = connection.transport  # type: ignore
                    transport.set_keepalive(keepalive)
        except (paramiko.SSHException, socket.gaierror) as err:
            raise SSHConnectionError(node_hostname=self.hostname, error=err)

        self.connection = connection
        self.transforms = transforms

    def with_transforms(self, *transforms: Callable[[str], str]) -> Self:
        return type(self)(
            hostname=self.hostname,
            connection=self.connection,
            transforms=(*self.transforms, *transforms),
        )

    def wrap(self, wrapper: str) -> Self:
        return self.with_transforms(wrapper.format)

    def with_precommand(self, precommand: str) -> Self:
        return self.wrap(f"{precommand} && {{}}")

    def with_profile(self, profile: str) -> Self:
        return self.wrap(f"source {profile} && {{}}")

    def with_bash(self) -> Self:
        return self.with_transforms(lambda cmd: shlex.join(["bash", "-c", cmd]))

    def display(self, cmd: str) -> None:
        print(T.bold_cyan(f"({self.hostname}) $ ", cmd))

    def _run(
        self,
        cmd: str,
        hide: Hide = False,
        warn: bool = False,
        asynchronous: bool = False,
        out_stream: TextIO | None = None,
        **kwargs,
    ) -> invoke.runners.Result | invoke.runners.Promise:
        try:
            # NOTE: See invoke.runners.Runner.run for possible **kwargs
            # invoke.runners.Runner.run
            return self.connection.run(
                cmd,
                hide=hide,
                warn=warn,
                asynchronous=asynchronous,
                out_stream=out_stream,
                **kwargs,
            )
        except socket.gaierror:
            exit(
                f"Error: Could not connect to host '{self.hostname}', "
                f"did you run 'mila init'?"
            )

    def simple_run(self, cmd: str):
        return self._run(cmd, hide=True, in_stream=False)

    @overload
    def run(
        self,
        cmd: str,
        *,
        display: bool | None = None,
        hide: Hide = False,
        warn: bool = False,
        asynchronous: Literal[False] = False,
        out_stream: TextIO | None = None,
        in_stream: TextIO | bool = False,
        **kwargs,
    ) -> invoke.runners.Result:
        ...

    @overload
    def run(
        self,
        cmd: str,
        *,
        display: bool | None = None,
        hide: Hide = False,
        warn: bool = False,
        asynchronous: Literal[True] = True,
        out_stream: TextIO | None = None,
        in_stream: TextIO | bool = False,
        **kwargs,
    ) -> invoke.runners.Promise:
        ...

    @overload
    def run(
        self,
        cmd: str,
        *,
        display: bool | None = None,
        hide: Hide = False,
        warn: bool = False,
        asynchronous: bool = ...,
        out_stream: TextIO | None = None,
        in_stream: TextIO | bool = False,
        **kwargs,
    ) -> invoke.runners.Result | invoke.runners.Promise:
        ...

    def run(
        self,
        cmd: str,
        display: bool | None = None,
        hide: Hide = False,
        warn: bool = False,
        asynchronous: bool = False,
        out_stream: TextIO | None = None,
        in_stream: TextIO | bool = False,
        **kwargs,
    ) -> invoke.runners.Promise | invoke.runners.Result:
        """Run a command on the remote host, returning the `invoke.Result`.

        NOTE: The arguments of this method are passed to `invoke.runners.Runner.run`.
        See that method for more info on the possible arguments.

        By the way, the \\'s  in the param descriptions below are there so that
        hovering over the arguments shows the entire description in code editors (i.e.
        VsCode for me).

        Parameters
        ----------
        cmd: The command to run
        display: Displays the host name and the command in colour before running it.
        hide: ``'out'`` (or ``'stdout'``) to hide only the stdout stream, \
            ``hide='err'`` (or ``'stderr'``) to hide only stderr, or ``hide='both'`` \
            (or ``True``) to hide both streams.
        warn: Whether to warn and continue, instead of raising \
            `invoke.runners.UnexpectedExit`, when the executed command exits with a \
            nonzero status.
        out_stream: A file-like stream object to which the subprocess' standard output \
            should be written. If ``None`` (the default), ``sys.stdout`` will be used.

        asynchronous : Whether to run the command asynchronously or not.

        Returns
        -------
        an `invoke.Result` if ``asynchronous=False``, else an `invoke.Promise`.
        """
        # NOTE: See invoke.runners.Runner.run for possible values in **kwargs
        if display is None:
            display = not hide
        if display:
            self.display(cmd)
        for transform in self.transforms:
            cmd = transform(cmd)
        return self._run(
            cmd,
            hide=hide,
            warn=warn,
            asynchronous=asynchronous,
            out_stream=out_stream,
            in_stream=in_stream,
            **kwargs,
        )

    def get_output(
        self,
        cmd: str,
        display: bool | None = None,
        hide: Hide = False,
        warn: bool = False,
    ) -> str:
        return self.run(
            cmd,
            display=display,
            hide=hide,
            warn=warn,
            in_stream=False,
        ).stdout.strip()

    @deprecated(
        "This method will be removed because its name is misleading: This "
        "returns a list with all the words in the output instead of all the "
        "lines. Use get_output(cmd).splitlines() instead.",
        category=None,  # TODO: Remove this so a warning is raised at runtime.
    )
    def get_lines(
        self,
        cmd: str,
        hide: Hide = False,
        warn: bool = False,
    ) -> list[str]:
        return self.get_output(
            cmd,
            hide=hide,
            warn=warn,
        ).split()

    def extract(
        self,
        cmd: str,
        patterns: dict[str, str],
        wait: bool = False,
        pty: bool = True,
        hide: Hide = False,
        **kwargs,
    ) -> tuple[fabric.runners.Remote, dict[str, str]]:
        # TODO: We pass this `QueueIO` class to `connection.run`, which expects a
        # file-like object and defaults to sys.stdout (a TextIO). However they only use
        # the `write` and `flush` methods, which means that this QueueIO is actually
        # okay to use. If we wanted to be 100% legit with this, we should probably use
        # something like a `io.StringIO` here instead, and create an object that manages
        # reading from it, and pass that `io.StringIO` buffer to `self.run`.
        qio: TextIO = QueueIO()
        promise = self.run(
            cmd,
            hide=hide,
            asynchronous=True,
            out_stream=qio,
            pty=pty,
            **kwargs,
        )
        runner = promise.runner
        assert isinstance(runner, fabric.runners.Remote)

        results: dict[str, str] = {}
        try:
            for line in qio.readlines(lambda: runner.process_is_finished):
                print(line, end="")
                for name, patt in list(patterns.items()):
                    m = re.search(patt, line)
                    if m:
                        results[name] = m.groups()[0]
                        patterns.pop(name)
                        if not patterns and not wait:
                            return runner, results

                # Check what the job id is when we sbatch
                m = re.search("^Submitted batch job ([0-9]+)", line)
                if m:
                    results["batch_id"] = m.groups()[0]
        except KeyboardInterrupt:
            runner.kill()
            if "batch_id" in results:
                # We need to preemptively cancel the job so that it doesn't
                # clutter the user's squeue when they Ctrl+C
                self.simple_run(f"scancel {results['batch_id']}")
            raise
        promise.join()
        return runner, results

    def get(
        self, remote: PurePosixPath | str, local: str | None
    ) -> fabric.transfer.Result:
        return self.connection.get(remote, local)

    def put(
        self, local: str | Path, remote: PurePosixPath | str
    ) -> fabric.transfer.Result:
        return self.connection.put(local, str(remote))

    def puttext(self, text: str, dest: PurePosixPath | str) -> None:
        base = PurePosixPath(dest).parent
        self.simple_run(f"mkdir -p {base}")
        with tempfile.NamedTemporaryFile("w") as f:
            f.write(text)
            f.flush()
            # BUG: Getting permission denied errors here on Windows!
            self.put(f.name, dest)

    def home(self) -> str:
        return self.simple_run("echo $HOME").stdout.strip()

    def persist(self):
        # TODO: I don't really understand why this is here.
        qn.print(
            "Warning: --persist does not work with --node or --job",
            style="orange",
        )
        return self

    def ensure_allocation(self) -> tuple[NodeNameDict, None]:
        return {"node_name": self.hostname}, None

    @deprecated(
        "Seems to be unused, so we'll remove it. Don't start using it.", category=None
    )
    def run_script(self, name: str, *args: str, **kwargs):
        # TODO: This method doesn't seem to be used.
        base = ".milatools/scripts"
        dest = f"{base}/{name}"
        print(T.bold_cyan(f"({self.hostname}) WRITE ", dest))
        self.simple_run(f"mkdir -p {base}")
        self.put(here / name, dest)
        return self.run(shlex.join([dest, *args]), **kwargs)

    @deprecated(
        "Seems to be unused, so we'll remove it. Don't start using it.", category=None
    )
    def extract_script(
        self,
        name: str,
        *args: str,
        pattern: dict[str, re.Pattern[str] | str],
        **kwargs,
    ):
        # TODO: This method doesn't seem to be used.
        base = ".milatools/scripts"
        dest = f"{base}/{name}"
        print(T.bold_cyan(f"({self.hostname}) WRITE ", dest))
        self.simple_run(f"mkdir -p {base}")
        self.put(here / name, dest)
        return self.extract(shlex.join([dest, *args]), pattern=pattern, **kwargs)


@deprecated("SlurmRemote is being deprecated. Use ComputeNode instead.", category=None)
class SlurmRemote(RemoteV1):
    def __init__(
        self,
        connection: fabric.Connection,
        alloc: list[str],
        transforms: Sequence[Callable[[str], str]] = (),
        persist: bool = False,
        hostname: str = "->",
    ):
        self.alloc = alloc
        self._persist = persist
        super().__init__(
            hostname=hostname,
            connection=connection,
            transforms=[
                *transforms,
                self.srun_transform_persist if persist else self.srun_transform,
            ],
        )

    def srun_transform(self, cmd: str) -> str:
        cmd = shlex.join(["srun", *self.alloc, "bash", "-c", cmd])
        # We need to cd to $SCRATCH before we can run jobs with `srun` on some clusters.
        cmd = f"cd $SCRATCH && {cmd}"
        return cmd

    def srun_transform_persist(self, cmd: str) -> str:
        tag = time.time_ns()
        remote_home = self.home()
        batch_file = PurePosixPath(remote_home) / f".milatools/batch/batch-{tag}.sh"
        output_file = PurePosixPath(remote_home) / f".milatools/batch/out-{tag}.txt"
        batch = batch_template.format(
            command=cmd,
            output_file=output_file,
            control_file=control_file_var.get(),
        )
        self.puttext(text=batch, dest=batch_file)
        self.simple_run(f"chmod +x {batch_file}")
        cmd = shlex.join(["sbatch", *self.alloc, str(batch_file)])
        # We need to cd to $SCRATCH before we run `sbatch` on some SLURM clusters.
        cmd = f"cd $SCRATCH && {cmd}"
        return f"{cmd}; touch {output_file}; tail -n +1 -f {output_file}"

    def with_transforms(
        self, *transforms: Callable[[str], str], persist: bool | None = None
    ):
        return SlurmRemote(
            connection=self.connection,
            alloc=self.alloc,
            transforms=[*self.transforms[:-1], *transforms],
            persist=self._persist if persist is None else persist,
        )

    def persist(self):
        return self.with_transforms(persist=True)

    def ensure_allocation(
        self,
    ) -> tuple[NodeNameDict | NodeNameAndJobidDict, fabric.runners.Remote]:
        """Makes the `salloc` or `sbatch` call and waits until the job starts.

        When `persist=True`:
        - uses `sbatch`
        - returns a tuple with:
            - a dict with the compute node name and the jobid
            - a `fabric.runners.Remote` object connected to the *login* node.

        When `persist=False`:
        - uses `salloc`
        - returns a tuple with:
            - a dict with the compute node name (without the jobid)
            - a `fabric.runners.Remote` object connected to the *login* node.
        """

        if self._persist:
            login_node_runner, results = self.extract(
                "echo @@@ $SLURMD_NODENAME @@@ && sleep 1000d",
                patterns={
                    "node_name": "@@@ ([^ ]+) @@@",
                    "jobid": "Submitted batch job ([0-9]+)",
                },
                hide=True,
            )
            node_name = get_first_node_name(results["node_name"])
            return {
                "node_name": node_name,
                "jobid": results["jobid"],
            }, login_node_runner
        else:
            remote = RemoteV1(hostname=self.hostname, connection=self.connection)
            command = shlex.join(["salloc", *self.alloc])
            # We need to cd to $SCRATCH before we can run `salloc` on some clusters.
            command = f"cd $SCRATCH && {command}"

            proc, results = remote.extract(
                command,
                patterns={"node_name": "salloc: Nodes ([^ ]+) are ready for job"},
            )

            # The node name can look like 'cn-c001', or 'cn-c[001-003]', or
            # 'cn-c[001,008]', or 'cn-c001,rtx8', etc. We will only connect to
            # a single one, though, so we will simply pick the first one.
            node_name = get_first_node_name(results["node_name"])
            # TODO: Is the `remote` object here connected to the compute node?
            # remote.hostname = node_name
            return {
                "node_name": node_name,
                # TODO: This would also work, there is output with the jobid in salloc.
                # "jobid": results["jobid"],
            }, proc
