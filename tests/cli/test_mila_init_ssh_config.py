from __future__ import annotations
from operator import index
from pathlib import Path
import textwrap
from typing import Callable
import pytest
from milatools.cli.commands import setup_ssh_config_interactive

from milatools.cli.utils import SSHConfig

expected_block_mila = r"""\
Host mila
  HostName login.server.mila.quebec
  User {user}
  PreferredAuthentications publickey,keyboard-interactive
  Port 2222
  ServerAliveInterval 120
  ServerAliveCountMax 5
"""

expected_block_mila_cpu = r"""\
Host mila-cpu
  User {user}
  Port 2222
  ForwardAgent yes
  StrictHostKeyChecking no
  LogLevel ERROR
  UserKnownHostsFile /dev/null
  RequestTTY force
  ConnectTimeout 600
  ProxyCommand ssh mila "salloc --partition=unkillable --dependency=singleton --cpus-per-task=2 --mem=16G /usr/bin/env bash -c 'nc \$SLURM_NODELIST 22'"
  RemoteCommand srun --mem=16G --cpus-per-task=2 --pty /usr/bin/env bash -l
"""


expected_block_mila_gpu = r"""\
Host mila-gpu
  User {user}
  Port 2222
  ForwardAgent yes
  StrictHostKeyChecking no
  LogLevel ERROR
  UserKnownHostsFile /dev/null
  RequestTTY force
  ConnectTimeout 600
  ProxyCommand ssh mila "salloc --partition=unkillable --dependency=singleton --cpus-per-task=2 --mem=16G --gres=gpu:1 /usr/bin/env bash -c 'nc \$SLURM_NODELIST 22'"
  RemoteCommand srun --mem=16G --cpus-per-task=2 --gres=gpu:1 --pty /usr/bin/env bash -l
"""


expected_block_compute_node = r"""\
Host *.server.mila.quebec !*login.server.mila.quebec
  HostName %h
  User {user}
  ProxyJump mila
"""

import prompt_toolkit
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from questionary import prompt
from questionary.prompts import prompt_by_name
from questionary.utils import is_prompt_toolkit_3
from prompt_toolkit.input.base import PipeInput

prompt_toolkit_version = tuple([int(v) for v in prompt_toolkit.VERSION])


def execute_with_input_pipe(func: Callable):
    if prompt_toolkit_version < (3, 0, 29):
        inp = create_pipe_input()
        try:
            return func(inp)
        finally:
            inp.close()
    else:
        with create_pipe_input() as inp:
            return func(inp)


def _shorten_if_not_none(s: str, width: int = 30) -> str:
    if s is None:
        return s
    return textwrap.shorten(textwrap.dedent(s), width=width, placeholder="...")


@pytest.mark.parametrize(
    "initial_contents, prompt_inputs, expected_contents",
    [
        (
            # Start without an .ssh/config file,
            None,
            # Don't accept creating the file. (first prompt), but accept everything else if asked.
            ["n", "bob", "y", ...],
            # Shoud NOT create a .ssh/config file.
            None,
        ),
        (
            # Start without a .ssh/config file,
            None,
            # Accept creating the file, then enter a username and accept all the prompts after that
            ["y", "bob", "y", ...],
            # Shoud a .ssh/config file with the following contents:
            "\n\n".join(
                [
                    expected_block_mila,
                    expected_block_mila_cpu,
                    expected_block_mila_gpu,
                    expected_block_compute_node,
                ]
            ).format(user="bob"),
        ),
        (
            # Start without a .ssh/config file,
            None,
            # Accept creating the file, then enter a username and then reject the next prompt.
            ["y", "bob", "n"],
            # Shoud create an empty .ssh/config file.
            "",
        ),
        (
            # Start with an empty file
            "",
            # Enter a username and accept all the prompts after that.
            ["bob", "y", ...],
            # Shoud produce the following contents:
            "\n\n".join(
                [
                    expected_block_mila,
                    expected_block_mila_cpu,
                    expected_block_mila_gpu,
                    expected_block_compute_node,
                ]
            ).format(user="bob"),
        ),
        (
            # Start with an empty file
            "",
            # Enter a username, accept the mila prompt, reject the next prompt.
            ["bob", "y", "n"],
            # Shoud produce the following contents:
            expected_block_mila.format(user="bob"),
        ),
        (
            # Start with an empty file
            "",
            # Enter a username, accept the mila prompt, accept the mila-cpu prompt, reject the next
            # one.
            ["bob", "y", "y", "n"],
            # Shoud produce the following contents:
            "\n\n".join(
                [
                    expected_block_mila,
                    expected_block_mila_cpu,
                ]
            ).format(user="bob"),
        ),
        (
            # Start with an empty file
            "",
            # Enter a username, accept the mila prompt, accept the mila-cpu prompt, accept the
            # mila-gpu prompt. Reject creating an entry for the compute node access.
            ["bob", "y", "y", "y", "n"],
            # Shoud produce the following contents:
            "\n\n".join(
                [
                    expected_block_mila,
                    expected_block_mila_cpu,
                    expected_block_mila_gpu,
                ]
            ).format(user="bob"),
        ),
        (
            # Start with an empty file
            "",
            # Enter a username, accept the mila prompt, accept the mila-cpu prompt, accept the
            # mila-gpu prompt. Accept creating an entry for the compute node access.
            ["bob", "y", "y", "y", "y"],
            # Shoud produce the following contents:
            "\n\n".join(
                [
                    expected_block_mila,
                    expected_block_mila_cpu,
                    expected_block_mila_gpu,
                    expected_block_compute_node,
                ]
            ).format(user="bob"),
        ),
        (
            # Start with a file with the overly general *.server.mila.quebec entry.
            """\
            Host *.server.mila.quebec
              HostName %h
              User bob
              ProxyJump mila
            """,
            # Enter a username, accept the mila prompt, accept the mila-cpu prompt, accept the
            # mila-gpu prompt. REJECT fixing the *.server.mila.quebec entry
            ["bob", "y", "y", "y", "y", "n"],
            # Shoud produce the following contents:
            "\n\n".join(
                [
                    expected_block_mila,
                    expected_block_mila_cpu,
                    expected_block_mila_gpu,
                    expected_block_compute_node,
                    textwrap.dedent(
                        """\
                        Host *.server.mila.quebec
                          HostName %h
                          User bob
                          ProxyJump mila
                        """
                    ),
                ]
            ).format(user="bob"),
        ),
        (
            # Start with a file with the overly general *.server.mila.quebec entry.
            """\
            Host *.server.mila.quebec
              HostName %h
              User bob
              ProxyJump mila
            """,
            # Enter a username, accept the mila prompt, accept the mila-cpu prompt, accept the
            # mila-gpu prompt. ACCEPT fixing the *.server.mila.quebec entry
            ["bob", "y", "y", "y", "y", "y"],
            # Shoud produce the following contents:
            "\n\n".join(
                [
                    expected_block_mila,
                    expected_block_mila_cpu,
                    expected_block_mila_gpu,
                    expected_block_compute_node,
                    textwrap.dedent(
                        """\
                        Host *.server.mila.quebec !*login.server.mila.quebec
                          HostName %h
                          User bob
                          ProxyJump mila
                        """
                    ),
                ]
            ).format(user="bob"),
        ),
        (
            # Start with a non-empty empty file,
            """\
            # a comment in a fake ssh config file
            """,
            # enter user and accept all the prompts after that.
            ["bob", "y", ...],
            # Shoud produce the following contents:
            "\n\n".join(
                [
                    "# a comment in a fake ssh config file",
                    expected_block_mila,
                    expected_block_mila_cpu,
                    expected_block_mila_gpu,
                    expected_block_compute_node,
                ]
            ).format(user="bob"),
        ),
    ],
    # ids=lambda tuple: (_shorten_if_not_none(tuple[0]), tuple[1], _shorten_if_not_none(tuple[2])),
)
def test_mila_init_ssh_config(
    initial_contents: str | None,
    prompt_inputs: list[str],
    expected_contents: str | None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    """Checks what entries are added to the ssh config file when running the corresponding portion of `mila init`."""
    # TODO: This doesn't completely work with the `questionary` package yet.

    _set_user_inputs(prompt_inputs, monkeypatch, tmp_path)

    ssh_config_path = tmp_path / ".ssh" / "config"
    ssh_config_path.parent.mkdir(parents=True, exist_ok=False)

    if initial_contents is not None:
        with open(ssh_config_path, "w") as f:
            f.write(textwrap.dedent(initial_contents))

    setup_ssh_config_interactive(ssh_config_path=ssh_config_path)

    if expected_contents:
        expected_contents = textwrap.dedent(expected_contents)

    # NOTE: this will stay None if the file wasn't created.
    resulting_contents: str | None = None
    if ssh_config_path.exists():
        with open(ssh_config_path, "r") as f:
            resulting_contents = f.read()

    assert resulting_contents == expected_contents


import io


def _set_user_inputs(
    prompt_inputs: str | list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):

    _prompt_inputs = [prompt_inputs] if isinstance(prompt_inputs, str) else prompt_inputs.copy()

    def _fake_input(_prompt: str) -> str:
        if len(_prompt_inputs) == 2 and _prompt_inputs[1] is Ellipsis:
            # The second item is '...', just to make the test easier to read. Return the first item.
            return _prompt_inputs[0]
        elif len(_prompt_inputs) >= 2:
            # Consume one value.
            return _prompt_inputs.pop(0)
        else:
            # Down to one item, return it over and over again.
            return _prompt_inputs[0]

    # NOTE: This works fine with the regular `input` function, but not with `questionary`.
    monkeypatch.setattr("builtins.input", _fake_input)

    from prompt_toolkit.input.defaults import create_pipe_input

    # TODO: It's a little bit hard for me to pass the pre-defined inputs to the prompts!
    # I'd need some help with this @breuleux
    # with create_pipe_input() as inp:
    #     inp.send_text("n\n" * 100 + "\n")
    #     inp.close()
    #     monkeypatch.setattr("sys.stdin", inp)

    # fake_stdin_file = tmp_path / "fake_stdin"
    # with open(fake_stdin_file, "w") as fake_stdin:
    # fake_stdin.writelines(["n\n"] * 100 + [""])
    # fake_stdin = open(fake_stdin_file, "r")
    # monkeypatch.setattr("sys.stdin", fake_stdin)
