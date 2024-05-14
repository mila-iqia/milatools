from __future__ import annotations

import contextvars
import functools
import itertools
import multiprocessing
import random
import shutil
import socket
import subprocess
import sys
import typing
import warnings
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal, TypeVar, Union, get_args

import blessed
import paramiko
import questionary as qn
from invoke.exceptions import UnexpectedExit
from sshconf import ConfigLine, SshConfigFile, read_ssh_config
from typing_extensions import ParamSpec, TypeGuard

if typing.TYPE_CHECKING:
    from milatools.utils.remote_v1 import RemoteV1


control_file_var = contextvars.ContextVar("control_file", default="/dev/null")

SSH_CONFIG_FILE = Path.home() / ".ssh" / "config"
SSH_CACHE_DIR = Path.home() / ".cache" / "ssh"


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
# custom clusters via config files.
CLUSTERS: list[Cluster] = list(
    get_args(ClusterWithInternetOnCNodes) + get_args(ClusterWithoutInternetOnCNodes)
)
DRAC_CLUSTERS: list[Cluster] = [c for c in CLUSTERS if c != "mila"]

cluster_to_connect_kwargs: dict[str, dict[str, Any]] = {
    "mila": {
        "banner_timeout": 60,
    }
}
"""The `connect_kwargs` dict to be passed to `fabric.Connection` for each cluster.

NOTE: These are passed down to `paramiko.SSHClient.connect`. See that method for all
the possible values.
"""


def currently_in_a_test() -> bool:
    """Returns True during unit tests (pytest) and False during normal execution."""
    return "pytest" in sys.modules


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
def with_control_file(remote: RemoteV1, name=None):
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
    def __init__(self, node_hostname: str, error: Exception):
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
            + f"-Try to exclude the node with -x {self.node_hostname} parameter\n"
            + "\n"
            + "If you reach out for help, you might want to also include this detailed error message:\n"
            + "\n```\n"
            + str(self.error)
            + "\n```\n"
        )


def yn(prompt: str, default: bool = True) -> bool:
    return qn.confirm(prompt, default=default).unsafe_ask()


def askpath(prompt: str, remote: RemoteV1) -> str:
    while True:
        pth = qn.text(prompt).unsafe_ask()
        try:
            remote.simple_run(f"[ -d {pth} ]")
        except UnexpectedExit:
            qn.print(f"Path {pth} does not exist")
            continue
        return pth


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


def get_hostname_to_use_for_compute_node(
    node_name: str, cluster: str = "mila", ssh_config_path: Path = SSH_CONFIG_FILE
) -> str:
    """Return the hostname to use to connect to this compute node via ssh."""
    if not ssh_config_path.exists():
        # If the SSH config file doesn't exist, we can't do much.
        raise MilatoolsUserError(
            f"SSH Config doesn't exist at {ssh_config_path}, did you run `mila init`?"
        )

    ssh_config = paramiko.SSHConfig.from_path(str(ssh_config_path))

    # If there is an entry matching for the compute node name (cn-a001) and there
    # isn't one matching the fully qualified compute node name
    # (cn-a001.(...).quebec),
    # then use the compute node name.

    def should_be_used_to_connect(hostname: str) -> bool:
        """Returns whether `hostname` should be used to run `ssh {hostname}`.

        Returns True if an entry matches `hostname` and returns a different hostname to
        use, or if the "proxyjump" option is set.
        """
        options = ssh_config.lookup(hostname)
        return bool(options.get("proxyjump")) or options["hostname"] != hostname

    if should_be_used_to_connect(node_name):
        # There is an entry in the sshconfig for e.g. `cn-a001` that sets the
        # hostname to use as `cn-a001.(...).quebec` or similar.
        return node_name
    if cluster == "mila" and should_be_used_to_connect(
        fully_qualified_name := f"{node_name}.server.mila.quebec"
    ):
        return fully_qualified_name
    warnings.warn(
        UserWarning(
            f"Unable to find the hostname to use to connect to node {node_name} of "
            f"the {cluster} cluster.\n"
            f"Assuming that we can ssh directly to {node_name} for now. To fix "
            f"this, consider adding an entry that matches the compute node "
            f"{node_name} in the SSH config file at {ssh_config_path}"
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


@functools.lru_cache
def running_inside_WSL() -> bool:
    return sys.platform == "linux" and bool(shutil.which("powershell.exe"))


P = ParamSpec("P")


def make_process(
    target: Callable[P, Any], *args: P.args, **kwargs: P.kwargs
) -> multiprocessing.Process:
    # Tiny wrapper around the `multiprocessing.Process` init to detect if the args and
    # kwargs don't match the target signature using typing instead of at runtime.
    return multiprocessing.Process(
        target=target, daemon=False, args=args, kwargs=kwargs
    )


V = TypeVar("V")


def batched(
    iterable: Iterable[V], n: int, droplast: bool = False
) -> Iterable[tuple[V, ...]]:
    """Yield successive n-sized chunks from iterable.

    if `droplast` is True, the last batch will be dropped if it's not full.

    >>> list(batched('ABCDEFG', 3))
    [('A', 'B', 'C'), ('D', 'E', 'F'), ('G',)]
    >>> list(batched('ABCDEFG', 3, droplast=True))
    [('A', 'B', 'C'), ('D', 'E', 'F')]
    """
    if sys.version_info >= (3, 12) and not droplast:
        return itertools.batched(iterable, n)
    if n < 1:
        raise ValueError("n must be at least one")
    it = iter(iterable)
    while batch := tuple(itertools.islice(it, n)):
        if len(batch) < n and droplast:
            break
        yield batch


def stripped_lines_of(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines()]


if sys.version_info < (3, 9):

    def removesuffix(s: str, suffix: str) -> str:
        """Backport of `str.removesuffix` for Python<3.9."""
        if s.endswith(suffix):
            return s[: -len(suffix)]
        else:
            return s
else:
    removesuffix = str.removesuffix
