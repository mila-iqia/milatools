import re
import socket
import tempfile
import time
from pathlib import Path
from queue import Empty, Queue
import paramiko

import questionary as qn
from fabric import Connection

from .utils import T, control_file_var, here, shjoin, SSHConnectionError

batch_template = """#!/bin/bash
#SBATCH --output={output_file}
#SBATCH --ntasks=1

echo jobid = $SLURM_JOB_ID >> {control_file}

{command}
"""


class QueueIO:
    def __init__(self):
        self.q = Queue()

    def write(self, s):
        self.q.put(s)

    def flush(self):
        pass

    def readlines(self, stop):
        current = ""
        lines = tuple()
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
    """Returns the name of the first node that was granted, given the string
    that salloc outputs to stdout.

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


class Remote:
    def __init__(self, hostname, connection=None, transforms=(), keepalive=60):
        self.hostname = hostname
        try:
            if connection is None:
                connection = Connection(hostname)
                if keepalive:
                    connection.open()
                    connection.transport.set_keepalive(keepalive)
        except paramiko.SSHException as err:
            raise SSHConnectionError(node_hostname=self.hostname, error=err)
        except Exception as err:
            raise err
        self.connection = connection
        self.transforms = transforms

    def with_transforms(self, *transforms):
        return Remote(
            hostname=self.hostname,
            connection=self.connection,
            transforms=(*self.transforms, *transforms),
        )

    def wrap(self, wrapper):
        return self.with_transforms(wrapper.format)

    def with_precommand(self, precommand):
        return self.wrap(f"{precommand} && {{}}")

    def with_profile(self, profile):
        return self.wrap(f"source {profile} && {{}}")

    def with_bash(self):
        return self.with_transforms(lambda cmd: shjoin(["bash", "-c", cmd]))

    def display(self, cmd):
        print(T.bold_cyan(f"({self.hostname}) $ ", cmd))

    def _run(self, cmd, **kwargs):
        try:
            return self.connection.run(cmd, **kwargs)
        except socket.gaierror:
            exit(
                f"Error: Could not connect to host '{self.hostname}', did you run 'mila init'?"
            )

    def simple_run(self, cmd, **kwargs):
        return self._run(cmd, hide=True, **kwargs)

    def run(self, cmd, display=None, hide=False, **kwargs):
        if display is None:
            display = not hide
        if display:
            self.display(cmd)
        for transform in self.transforms:
            cmd = transform(cmd)
        return self._run(cmd, hide=hide, **kwargs)

    def get_output(self, cmd, **kwargs):
        return self.run(cmd, **kwargs).stdout.strip()

    def get_lines(self, cmd, **kwargs):
        return self.get_output(cmd, **kwargs).split()

    def extract(self, cmd, patterns, wait=False, **kwargs):
        kwargs.setdefault("pty", True)
        qio = QueueIO()
        proc = self.run(cmd, asynchronous=True, out_stream=qio, **kwargs)
        results = {}
        try:
            for line in qio.readlines(lambda: proc.runner.process_is_finished):
                print(line, end="")
                for name, patt in list(patterns.items()):
                    m = re.search(patt, line)
                    if m:
                        results[name] = m.groups()[0]
                        patterns.pop(name)
                        if not patterns and not wait:
                            return proc.runner, results

                # Check what the job id is when we sbatch
                m = re.search("^Submitted batch job ([0-9]+)", line)
                if m:
                    results["batch_id"] = m.groups()[0]
        except KeyboardInterrupt:
            proc.runner.kill()
            if "batch_id" in results:
                # We need to preemptively cancel the job so that it doesn't
                # clutter the user's squeue when they Ctrl+C
                self.simple_run(f"scancel {results['batch_id']}")
            raise
        proc.join()
        return proc.runner, results

    def get(self, src, dest):
        return self.connection.get(src, dest)

    def put(self, src, dest):
        return self.connection.put(src, dest)

    def puttext(self, text, dest):
        base = Path(dest).parent
        self.simple_run(f"mkdir -p {base}")
        with tempfile.NamedTemporaryFile("w") as f:
            f.write(text)
            f.flush()
            self.put(f.name, dest)

    def home(self):
        return self.get_output("echo $HOME", hide=True)

    def persist(self):
        qn.print(
            "Warning: --persist does not work with --node or --job", style="orange"
        )
        return self

    def ensure_allocation(self):
        return {"node_name": self.hostname}, None

    def run_script(self, name, *args, **kwargs):
        base = ".milatools/scripts"
        dest = f"{base}/{name}"
        print(T.bold_cyan(f"({self.host}) WRITE ", dest))
        self.simple_run(f"mkdir -p {base}")
        self.put(here / name, dest)
        return self.run(shjoin([dest, *args]), **kwargs)

    def extract_script(self, name, *args, pattern, **kwargs):
        base = ".milatools/scripts"
        dest = f"{base}/{name}"
        print(T.bold_cyan(f"({self.host}) WRITE ", dest))
        self.simple_run(f"mkdir -p {base}")
        self.put(here / name, dest)
        return self.extract(shjoin([dest, *args]), pattern=pattern, **kwargs)


class SlurmRemote(Remote):
    def __init__(self, connection, alloc, transforms=(), persist=False):
        self.alloc = alloc
        self._persist = persist
        super().__init__(
            hostname="->",
            connection=connection,
            transforms=[
                *transforms,
                self.srun_transform_persist if persist else self.srun_transform,
            ],
        )

    def srun_transform(self, cmd):
        return shjoin(["srun", *self.alloc, "bash", "-c", cmd])

    def srun_transform_persist(self, cmd):
        tag = time.time_ns()
        batch_file = f".milatools/batch/batch-{tag}.sh"
        output_file = f".milatools/batch/out-{tag}.txt"
        batch = batch_template.format(
            command=cmd,
            output_file=output_file,
            control_file=control_file_var.get(),
        )
        self.puttext(batch, batch_file)
        cmd = shjoin(["sbatch", *self.alloc, batch_file])
        return f"{cmd}; touch {output_file}; tail -n +1 -f {output_file}"

    def with_transforms(self, *transforms, persist=None):
        return SlurmRemote(
            connection=self.connection,
            alloc=self.alloc,
            transforms=[*self.transforms[:-1], *transforms],
            persist=self._persist if persist is None else persist,
        )

    def persist(self):
        return self.with_transforms(persist=True)

    def ensure_allocation(self):
        if self._persist:
            proc, results = self.extract(
                "echo @@@ $(hostname) @@@ && sleep 1000d",
                patterns={
                    "node_name": "@@@ ([^ ]+) @@@",
                    "jobid": "Submitted batch job ([0-9]+)",
                },
                hide=True,
            )
            node_name = get_first_node_name(results["node_name"])
            return {"node_name": node_name, "jobid": results["jobid"]}, proc
        else:
            remote = Remote(hostname="->", connection=self.connection).with_bash()
            proc, results = remote.extract(
                shjoin(["salloc", *self.alloc]),
                patterns={"node_name": "salloc: Nodes ([^ ]+) are ready for job"},
            )
            # The node name can look like 'cn-c001', or 'cn-c[001-003]', or
            # 'cn-c[001,008]', or 'cn-c001,rtx8', etc. We will only connect to a
            # single one, though, so we will simply pick the first one.
            node_name = get_first_node_name(results["node_name"])
            return {"node_name": node_name}, proc
