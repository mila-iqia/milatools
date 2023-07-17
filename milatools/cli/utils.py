from __future__ import annotations

import contextvars
import itertools
import random
import shlex
from contextlib import contextmanager
from pathlib import Path

import blessed
import paramiko
import questionary as qn
from invoke.exceptions import UnexpectedExit
from sshconf import read_ssh_config

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


class SSHConnectionError(Exception):
    def __init__(self, node_hostname: str, error: paramiko.SSHException):
        super().__init__()
        self.node_hostname = node_hostname
        self.error = error

    def __str__(self):
        return repr(
            f"An error happened while trying to establish a connection with {self.node_hostname}. "
            "Check the status of your connection and of the cluster by ssh'ing onto it. "
            "Workaround : try to exclude the node with -x [<node>] parameter"
            "\n"
            f"Exception: {self.error}"
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
def shjoin(split_command):
    """Return a shell-escaped string from *split_command*."""
    return " ".join(shlex.quote(arg) for arg in split_command)


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


def qualified(node_name):
    """Return the fully qualified name corresponding to this node name."""

    if "." not in node_name and not node_name.endswith(".server.mila.quebec"):
        node_name = f"{node_name}.server.mila.quebec"
    return node_name
