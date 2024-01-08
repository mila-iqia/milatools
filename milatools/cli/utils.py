from __future__ import annotations

import contextvars
import functools
import itertools
import multiprocessing
import random
import shlex
import shutil
import socket
import subprocess
import sys
import typing
import warnings
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterable, Union

import blessed
import paramiko
import questionary as qn
from invoke.exceptions import UnexpectedExit
from sshconf import ConfigLine, SshConfigFile, read_ssh_config
from typing_extensions import Literal, ParamSpec, TypeGuard, get_args

if typing.TYPE_CHECKING:
    from milatools.cli.remote import Remote

control_file_var = contextvars.ContextVar("control_file", default="/dev/null")


T = blessed.Terminal()

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

ClusterWithInternetOnCNodes = Literal["mila", "cedar"]
ClusterWithoutInternetOnCNodes = Literal["narval", "beluga", "graham"]

Cluster = Union[ClusterWithInternetOnCNodes, ClusterWithoutInternetOnCNodes]

# Introspect the type annotation above so we don't duplicate hard-coded values.
# NOTE: An alternative approach could also be to avoid hard-coding anything at all, but
# lose the benefits of rich typing. Perhaps we can opt for that at some point if we add
# support for more and more clusters, or want to make it possible for users to add
# custom clusters.
CLUSTERS: list[Cluster] = list(
    get_args(ClusterWithInternetOnCNodes) + get_args(ClusterWithoutInternetOnCNodes)
)


def no_internet_on_compute_nodes(
    cluster: Cluster,
) -> TypeGuard[ClusterWithoutInternetOnCNodes]:
    if cluster not in CLUSTERS:
        warnings.warn(
            UserWarning(
                f"Unknown cluster {cluster}. Assuming that compute nodes do not have "
                f"internet access on this cluster for now."
            )
        )
    return cluster not in get_args(ClusterWithInternetOnCNodes)


def randname():
    a = random.choice(syllables)
    b = random.choice(syllables)
    c = random.choice(syllables)
    d = random.choice(syllables)
    return f"{a}{b}-{c}{d}"


@contextmanager
def with_control_file(remote: Remote, name=None):
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


class MilatoolsUserError(Exception):
    pass


class CommandNotFoundError(MilatoolsUserError):
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


class SSHConnectionError(paramiko.SSHException):
    def __init__(self, node_hostname: str, error: paramiko.SSHException):
        super().__init__()
        self.node_hostname = node_hostname
        self.error = error

    def __str__(self):
        return (
            "An error happened while trying to establish a connection with "
            f"{self.node_hostname}"
            + "\n\t"
            + "-The cluster might be under maintenance"
            + "\n\t   "
            + "Check #mila-cluster for updates on the state of the cluster"
            + "\n\t"
            + "-Check the status of your connection to the cluster by ssh'ing onto it."
            + "\n\t"
            + "-Retry connecting with mila"
            + "\n\t"
            + f"-Try to exclude the node with -x {self.node_hostname} "
            "parameter"
        )


def yn(prompt: str, default: bool = True) -> bool:
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


# This is the implementation of shlex.join in Python >= 3.8
def shjoin(split_command: Iterable[str]) -> str:
    """Return a shell-escaped string from *split_command*."""
    return " ".join(shlex.quote(arg) for arg in split_command)


class SSHConfig:
    """Wrapper around sshconf with some extra niceties."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.cfg = read_ssh_config(path)
        # self.add = self.cfg.add
        self.remove = self.cfg.remove
        self.rename = self.cfg.rename
        # self.save = self.cfg.save
        self.host = self.cfg.host
        self.hosts = self.cfg.hosts
        self.set = self.cfg.set

    def add(
        self,
        host: str,
        _space_before: bool = True,
        _space_after: bool = False,
        **kwargs,
    ):
        """Add another host to the SSH configuration.

        Parameters
        ----------
        host: The Host entry to add.
        **kwargs: The parameters for the host (without "Host" parameter itself)
        """
        config_file: SshConfigFile = self.cfg.configs_[0][1]
        config_file_lines: list[ConfigLine] = config_file.lines_

        lines_before = config_file_lines.copy()
        # This modifies `self.cfg.configs_[0][1].lines_` (which is saved above).
        # See the source code of `SshConfigFile.add` for more details.
        self.cfg.add(host=host, **kwargs)

        def _is_empty_line(line: ConfigLine) -> bool:
            return vars(line) == vars(ConfigLine(line=""))

        if not _space_before:
            # Remove the empty line before this entry.
            empty_line = config_file_lines.pop(len(lines_before))
            assert _is_empty_line(empty_line)

        if not _space_after:
            # Remove the empty line after this added entry.
            empty_line = config_file_lines.pop()
            assert _is_empty_line(empty_line)

    def save(self) -> None:
        filename_to_configfile: list[tuple[str, SshConfigFile]] = self.cfg.configs_
        assert len(filename_to_configfile) == 1
        filename, configfile = filename_to_configfile[0]
        config_file_lines: list[ConfigLine] = configfile.lines_
        lines: list[str] = [x.line for x in config_file_lines]
        lines = [line.rstrip() for line in lines]
        with open(filename, "w") as fh:
            fh.write("\n".join(lines))

    def hoststring(self, host: str) -> str:
        lines = []
        for _filename, cfg in self.cfg.configs_:
            lines += [line.line for line in cfg.lines_ if line.host == host]
        return "\n".join(lines)


def get_fully_qualified_hostname_of_compute_node(
    node_name: str, cluster: str = "mila"
) -> str:
    """Return the fully qualified name corresponding to this node name."""
    if cluster == "mila":
        if node_name.endswith(".server.mila.quebec"):
            return node_name
        return f"{node_name}.server.mila.quebec"
    if cluster in CLUSTERS:
        # For the other explicitly supported clusters in the SSH config, the node name
        # of the compute node can be used directly with ssh from the local machine, no
        # need to use a fully qualified name.
        return node_name
    warnings.warn(
        UserWarning(
            f"Using a custom cluster {cluster}. Assuming that we can ssh directly to "
            f"its compute node {node_name!r}."
        )
    )
    return node_name


def get_fully_qualified_name() -> str:
    """Return the fully qualified name of the current machine.

    Much faster than socket.getfqdn() on Mac. Falls back to that if the hostname command
    is not available.
    """
    try:
        return subprocess.check_output(["hostname", "-f"]).decode("utf-8").strip()
    except Exception:
        # Fall back, e.g. on Windows.
        return socket.getfqdn()


@functools.lru_cache()
def running_inside_WSL() -> bool:
    return sys.platform == "linux" and bool(shutil.which("powershell.exe"))


P = ParamSpec("P")


def make_process(
    target: Callable[P, Any], *args: P.args, **kwargs: P.kwargs
) -> multiprocessing.Process:
    # Tiny wrapper around the `multiprocessing.Process` init to detect if the args and
    # kwargs don't match the target signature using typing instead of at runtime.
    return multiprocessing.Process(target=target, daemon=True, args=args, kwargs=kwargs)
