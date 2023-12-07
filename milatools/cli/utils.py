from __future__ import annotations

import contextvars
import functools
import itertools
import random
import shlex
import shutil
import socket
import subprocess
import sys
import typing
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

import blessed
import paramiko
import questionary as qn
from invoke.exceptions import UnexpectedExit
from sshconf import ConfigLine, SshConfigFile, read_ssh_config

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


def qualified(node_name):
    """Return the fully qualified name corresponding to this node name."""

    if "." not in node_name and not node_name.endswith(".server.mila.quebec"):
        node_name = f"{node_name}.server.mila.quebec"
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
