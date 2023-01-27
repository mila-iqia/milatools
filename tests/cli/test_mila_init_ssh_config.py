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
    "confirm_changes",
    [False, True],
)
@pytest.mark.parametrize(
    "accept_mila, accept_mila_cpu, accept_mila_gpu, accept_mila_computenode",
    list(itertools.product([False, True], repeat=4)),
)
@pytest.mark.parametrize(
    "initial_contents",
    [""],  # todo: test the case where the file is created separately?
)
def test_mila_init_empty_ssh_config(
    initial_contents: str | None,
    accept_mila: bool,
    accept_mila_cpu: bool,
    accept_mila_gpu: bool,
    accept_mila_computenode: bool,
    confirm_changes: bool,
    tmp_path: Path,
):
    """Checks what entries are added to the ssh config file when running the corresponding portion of `mila init`."""
    # TODO: This doesn't completely work with the `questionary` package yet.
    expected_blocks = []
    expected_blocks += [expected_block_mila] if accept_mila else []
    expected_blocks += [expected_block_mila_cpu] if accept_mila_cpu else []
    expected_blocks += [expected_block_mila_gpu] if accept_mila_gpu else []
    expected_blocks += [expected_block_compute_node] if accept_mila_computenode else []

    ssh_config_path = tmp_path / ".ssh" / "config"
    ssh_config_path.parent.mkdir(parents=True, exist_ok=False)

    if initial_contents is not None:
        with open(ssh_config_path, "w") as f:
            f.write(textwrap.dedent(initial_contents))

    def _yn(accept: bool):
        return "y" if accept else "n"

    user_inputs = [
        "bob\r",  # username
        _yn(accept_mila),
        _yn(accept_mila_cpu),
        _yn(accept_mila_gpu),
        _yn(accept_mila_computenode),
        _yn(confirm_changes),
    ]

    if not confirm_changes:
        expected_contents = initial_contents
    else:
        # TODO: Also test when there are already entries in the sshconfig file.
        expected_contents = _join_blocks(*expected_blocks)

    with set_user_inputs(user_inputs) as input_pipe:
        if not any([accept_mila, accept_mila_cpu, accept_mila_gpu, accept_mila_computenode]):
            # Won't get prompted for confirmation if no changes are made.
            should_exit = False
        else:
            should_exit = not confirm_changes
        with (pytest.raises(SystemExit) if should_exit else contextlib.nullcontext()):
            setup_ssh_config_interactive(ssh_config_path=ssh_config_path, _input=input_pipe)

    # NOTE: this will stay None if the file wasn't created.
    resulting_contents: str | None = None
    if ssh_config_path.exists():
        with open(ssh_config_path, "r") as f:
            resulting_contents = f.read()

    assert resulting_contents == expected_contents


@contextmanager
def set_user_inputs(prompt_inputs: list[str]):
    """NOTE: Important: send only 'y' or 'n', (not 'y\r' or 'n\r') if the prompt is on the same
    line! Otherwise the '\r' is passed to the next prompt, which uses the default value.
    """
    _prompt_inputs = prompt_inputs.copy()
    sent_prompts = []
    with create_pipe_input() as input_pipe:
        sent = 0
        while _prompt_inputs:
            to_send = _prompt_inputs.pop(0)
            input_pipe.send_text(to_send)
            sent_prompts.append(to_send)
            sent += 1
        yield input_pipe
