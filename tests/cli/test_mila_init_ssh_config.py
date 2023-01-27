from __future__ import annotations
from contextlib import contextmanager
import contextlib
import itertools
from pathlib import Path
import textwrap
import pytest
from milatools.cli.commands import setup_ssh_config_interactive
from prompt_toolkit.input.defaults import create_pipe_input
from milatools.cli.utils import SSHConfig

expected_block_mila = """
Host mila
  HostName login.server.mila.quebec
  User {user}
  PreferredAuthentications publickey,keyboard-interactive
  Port 2222
  ServerAliveInterval 120
  ServerAliveCountMax 5
"""

expected_block_mila_cpu = """
Host mila-cpu
  User {user}
  Port 2222
  ForwardAgent yes
  StrictHostKeyChecking no
  LogLevel ERROR
  UserKnownHostsFile /dev/null
  RequestTTY force
  ConnectTimeout 600
  ProxyCommand ssh mila "salloc --partition=unkillable --dependency=singleton --cpus-per-task=2 --mem=16G /usr/bin/env bash -c 'nc \\$SLURM_NODELIST 22'"
  RemoteCommand srun --cpus-per-task=2 --mem=16G --pty /usr/bin/env bash -l
"""


expected_block_mila_gpu = """
Host mila-gpu
  User {user}
  Port 2222
  ForwardAgent yes
  StrictHostKeyChecking no
  LogLevel ERROR
  UserKnownHostsFile /dev/null
  RequestTTY force
  ConnectTimeout 600
  ProxyCommand ssh mila "salloc --partition=unkillable --dependency=singleton --cpus-per-task=2 --mem=16G --gres=gpu:1 /usr/bin/env bash -c 'nc \\$SLURM_NODELIST 22'"
  RemoteCommand srun --cpus-per-task=2 --mem=16G --gres=gpu:1 --pty /usr/bin/env bash -l
"""


expected_block_compute_node = """
Host *.server.mila.quebec !*login.server.mila.quebec
  HostName %h
  User {user}
  ProxyJump mila
"""


def _join_blocks(*blocks: str, user: str = "bob") -> str:
    return "\n".join(blocks).format(user=user)


@pytest.mark.parametrize(
    "initial_contents",
    [None, ""],
)
@pytest.mark.parametrize(
    "accept_mila, accept_mila_cpu, accept_mila_gpu",
    list(itertools.product([True, False], repeat=3)),
)
@pytest.mark.parametrize(
    "confirm_changes",
    [True, False],
)
def test_mila_init_empty_ssh_config(
    initial_contents: str | None,
    accept_mila: bool,
    accept_mila_cpu: bool,
    accept_mila_gpu: bool,
    confirm_changes: bool,
    tmp_path: Path,
):
    """Checks what entries are added to the ssh config file when running the corresponding portion of `mila init`."""
    # TODO: This doesn't completely work with the `questionary` package yet.
    expected_blocks = []
    expected_blocks += [expected_block_mila] if accept_mila else []
    expected_blocks += [expected_block_mila_cpu] if accept_mila_cpu else []
    expected_blocks += [expected_block_mila_gpu] if accept_mila_gpu else []

    ssh_config_path = tmp_path / ".ssh" / "config"
    ssh_config_path.parent.mkdir(parents=True, exist_ok=False)

    if initial_contents is not None:
        with open(ssh_config_path, "w") as f:
            f.write(textwrap.dedent(initial_contents))

    def user_input(accept: bool):
        return ("y" if accept else "n") + "\r"

    user_inputs = [
        "bob" + "\r",  # username
        user_input(accept_mila),
        user_input(accept_mila_cpu),
        user_input(accept_mila_gpu),
        user_input(confirm_changes),
    ]

    with set_user_inputs(user_inputs) as input_pipe:
        with (pytest.raises(SystemExit) if not confirm_changes else contextlib.nullcontext()):
            setup_ssh_config_interactive(ssh_config_path=ssh_config_path, _input_pipe=input_pipe)

    if not confirm_changes:
        expected_contents = initial_contents
    else:
        # TODO: Also test when there are already entries in the sshconfig file.
        expected_contents = _join_blocks(*expected_blocks)

    # NOTE: this will stay None if the file wasn't created.
    resulting_contents: str | None = None
    if ssh_config_path.exists():
        with open(ssh_config_path, "r") as f:
            resulting_contents = f.read()

    assert resulting_contents == expected_contents


@contextmanager
def set_user_inputs(prompt_inputs: list[str]):
    _prompt_inputs = prompt_inputs.copy()

    with create_pipe_input() as input_pipe:
        sent = 0
        while sent < max(10, len(prompt_inputs)):
            to_send: str
            if len(_prompt_inputs) == 2 and _prompt_inputs[1] is Ellipsis:
                # The second item is '...', just to make the test easier to read. Return the first item.
                to_send = _prompt_inputs[0]
            else:
                # Consume one value.
                to_send = _prompt_inputs.pop(0)
            # print(to_send + "\r", end="")
            input_pipe.send_text(to_send + "\r")
            sent += 1
        yield input_pipe


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
