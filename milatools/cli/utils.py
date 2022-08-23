import contextvars
import itertools
import random
import re
import shlex
import socket
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from queue import Empty, Queue

import blessed
import questionary as qn
from fabric import Connection
from invoke.exceptions import UnexpectedExit
from sshconf import read_ssh_config

control_file_var = contextvars.ContextVar("control_file", default="/dev/null")

here = Path(__file__).parent

style = qn.Style(
    [
        ("envname", "yellow bold"),
        ("envpath", "cyan"),
        ("prefix", "bold"),
        ("special", "orange bold"),
        ("cancel", "grey bold"),
    ]
)

vowels = list("aeiou")
consonants = list("bdfgjklmnprstvz")
syllables = ["".join(letters) for letters in itertools.product(consonants, vowels)]


def randname():
    a = random.choice(syllables)
    b = random.choice(syllables)
    c = random.choice(syllables)
    d = random.choice(syllables)
    return f"{a}{b}-{c}{d}"


@contextmanager
def with_control_file(remote, name=None):
    name = name or randname()
    pth = f".milatools/control/{name}"
    remote.run("mkdir -p ~/.milatools/control", hide=True)

    try:
        remote.simple_run(f"[ -f {pth} ]")
        exit(f"Server {name} already exists. You may use mila serve kill to remove it.")
    except UnexpectedExit:
        pass

    token = control_file_var.set(pth)
    try:
        yield pth
    finally:
        control_file_var.reset(token)


batch_template = """#!/bin/bash
#SBATCH --output={output_file}
#SBATCH --ntasks=1

echo jobid = $SLURM_JOB_ID >> {control_file}

{command}
"""


class CommandNotFoundError(Exception):

    # Instructions to install certain commands if they are not found
    instructions = {
        "code": (
            "To fix this, try starting VSCode, then hit Cmd+Shift+P,"
            " type 'install code command' in the box, and hit Enter."
            " You might need to restart your shell."
        )
    }

    def __init__(self, command):
        super().__init__(command)

    def __str__(self):
        cmd = self.args[0]
        message = f"Command '{cmd}' does not exist locally."
        supp = self.instructions.get(cmd, None)
        if supp:
            message += f" {supp}"
        return message


def yn(prompt, default=True):
    return qn.confirm(prompt, default=default).unsafe_ask()


def askpath(prompt, remote):
    while True:
        pth = qn.text(prompt).unsafe_ask()
        try:
            remote.simple_run(f"[ -d {pth} ]")
        except UnexpectedExit:
            qn.print(f"Path {pth} does not exist")
            continue
        return pth


T = blessed.Terminal()


# This is the implementation of shlex.join in Python >= 3.8
def shjoin(split_command):
    """Return a shell-escaped string from *split_command*."""
    return " ".join(shlex.quote(arg) for arg in split_command)


class Local:
    def display(self, args):
        print(T.bold_green(f"(local) $ ", shjoin(args)))

    def silent_get(self, *args, **kwargs):
        return subprocess.check_output(
            args,
            universal_newlines=True,
            **kwargs,
        )

    def get(self, *args, **kwargs):
        self.display(args)
        return subprocess.check_output(
            args,
            universal_newlines=True,
            **kwargs,
        )

    def run(self, *args, **kwargs):
        self.display(args)
        try:
            return subprocess.run(
                args,
                universal_newlines=True,
                **kwargs,
            )
        except FileNotFoundError as e:
            if e.filename == args[0]:
                raise CommandNotFoundError(e.filename)
            else:
                raise

    def popen(self, *args, **kwargs):
        self.display(args)
        return subprocess.Popen(
            args,
            universal_newlines=True,
            **kwargs,
        )

    def check_passwordless(self, host):
        results = self.run(
            "ssh",
            "-oPreferredAuthentications=publickey",
            host,
            "echo OK",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if results.returncode != 0:
            if "Permission denied" in results.stderr:
                return False
            else:
                print(results.stdout)
                print(results.stderr)
                exit("Failed to connect to mila, could not understand error")
        else:
            print("# OK")
            return True


class QueueIO:
    def __init__(self):
        self.q = Queue()

    def write(self, s):
        self.q.put(s)

    def flush(self):
        pass

    def readlines(self, stop):
        current = ""
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


class Remote:
    def __init__(self, hostname, connection=None, transforms=()):
        self.hostname = hostname
        if connection is None:
            connection = Connection(hostname)
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
        return self.hostname, None

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
        remote = Remote(hostname="->", connection=self.connection).with_bash()
        proc, results = remote.extract(
            shjoin(["salloc", *self.alloc]),
            patterns={"node_name": "salloc: Nodes ([^ ]+) are ready for job"},
        )
        # The node name can look like 'cn-c001', or 'cn-c[001-003]', or
        # 'cn-c[001,008]', or 'cn-c001,rtx8', etc. We will only connect to a
        # single one, though, so we will simply pick the first one.
        node_name = get_first_node_name(results["node_name"])
        return node_name, proc


def get_first_node_name(node_names_out: str) -> str:
    """ Returns the name of the first node that was granted, given the string
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


class SSHConfig:
    """Wrapper around sshconf with some extra niceties."""

    def __init__(self, path):
        self.cfg = read_ssh_config(path)
        self.add = self.cfg.add
        self.remove = self.cfg.remove
        self.rename = self.cfg.rename
        self.save = self.cfg.save
        self.host = self.cfg.host
        self.hosts = self.cfg.hosts

    def hoststring(self, host):
        lines = []
        for filename, cfg in self.cfg.configs_:
            lines += [line.line for line in cfg.lines_ if line.host == host]
        return "\n".join(lines)

    def confirm(self, host):
        print(T.bold("The following code will be appended to your ~/.ssh/config:\n"))
        print(self.hoststring(host))
        return yn("\nIs this OK?")
