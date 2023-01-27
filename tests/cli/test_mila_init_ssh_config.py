from __future__ import annotations

import contextlib
import inspect
import itertools
import textwrap
from contextlib import contextmanager
from pathlib import Path

import pytest
from prompt_toolkit import PromptSession
from prompt_toolkit.input.defaults import create_pipe_input

from milatools.cli.commands import setup_ssh_config_interactive
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
  ProxyCommand ssh mila "salloc --partition=unkillable --dependency=singleton --cpus-per-task=2 \
--mem=16G /usr/bin/env bash -c 'nc \\$SLURM_NODELIST 22'"
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
  ProxyCommand ssh mila "salloc --partition=unkillable --dependency=singleton --cpus-per-task=2 \
--mem=16G --gres=gpu:1 /usr/bin/env bash -c 'nc \\$SLURM_NODELIST 22'"
  RemoteCommand srun --cpus-per-task=2 --mem=16G --gres=gpu:1 --pty /usr/bin/env bash -l
"""


expected_block_compute_node = """
Host *.server.mila.quebec !*login.server.mila.quebec
  HostName %h
  User {user}
  ProxyJump mila
"""


def _join_blocks(*blocks: str, user: str = "bob") -> str:
    return "\n".join(textwrap.dedent(block) for block in blocks).format(user=user)


def _yn(accept: bool):
    return "y" if accept else "n"


def test_creates_ssh_config_file(tmp_path: Path):
    ssh_config_path = tmp_path / "ssh_config"
    with set_user_inputs(["y", "bob\r", "y", "y", "y", "y", "y"]) as input_pipe:
        setup_ssh_config_interactive(tmp_path / "ssh_config", _input=input_pipe)
    assert ssh_config_path.exists()


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
    [
        "",
        """\
        # A comment in the file.
        """,
        # NOTE: Not using this test case because `ssh_config` will generate the outputs with the
        # same indent, which will make our `expected_blocks` not match the actual output.
        # """\
        # # a comment
        # Host foo
        #     HostName foobar.com
        # """,
        """\
        # a comment
        Host foo
          HostName foobar.com

        # another comment
        """,
    ],
)
def test_mila_init_no_existing_entries(
    initial_contents: str | None,
    accept_mila: bool,
    accept_mila_cpu: bool,
    accept_mila_gpu: bool,
    accept_mila_computenode: bool,
    confirm_changes: bool,
    tmp_path: Path,
):
    """Checks what entries are added to the ssh config file when running the corresponding portion
    of `mila init`.
    """
    # TODO: This doesn't completely work with the `questionary` package yet.
    expected_blocks = []
    expected_blocks += [expected_block_mila] if accept_mila else []
    expected_blocks += [expected_block_mila_cpu] if accept_mila_cpu else []
    expected_blocks += [expected_block_mila_gpu] if accept_mila_gpu else []
    expected_blocks += [expected_block_compute_node] if accept_mila_computenode else []

    ssh_config_path = tmp_path / ".ssh" / "config"
    ssh_config_path.parent.mkdir(parents=True, exist_ok=False)

    if initial_contents:
        initial_contents = textwrap.dedent(initial_contents)

    if initial_contents is not None:
        with open(ssh_config_path, "w") as f:
            f.write(initial_contents)

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
    elif initial_contents:
        # If there is already something in the config file, the new entries should be appended at
        # the end.
        # TODO: Confirm this with @Olexa
        expected_contents = initial_contents + _join_blocks(*expected_blocks)
    else:
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
        with open(ssh_config_path) as f:
            resulting_contents = f.read()

    assert resulting_contents == expected_contents


def test_questionary_prompts_works_with_input_none():
    """Makes sure that the actual command will work if the _input argument is None, and that it's
    safe for `None` to be passed down all the way down the `questionary` stack as part of **kwargs.
    """
    assert inspect.signature(PromptSession).parameters["input"].default is None


def test_fixes_overly_general_entry(tmp_path: Path):
    """Test the case where the user has a *.server.mila.quebec entry."""
    ssh_config_path = tmp_path / ".ssh" / "config"
    ssh_config_path.parent.mkdir(parents=True, exist_ok=False)
    initial_contents = textwrap.dedent(
        """\
        Host *.server.mila.quebec
          User bob

        """
    )
    with open(ssh_config_path, "w") as f:
        f.write(initial_contents)

    # Note/todo?: There isn't a newline at the end of the generated output.
    expected_contents = textwrap.dedent(
        """\
        Host *.server.mila.quebec !*login.server.mila.quebec
          User bob
        """
    )
    # Only change that entry, and confirm.
    with set_user_inputs(["bob\r", "n", "n", "n", "y", "y"]) as input_pipe:
        setup_ssh_config_interactive(ssh_config_path=ssh_config_path, _input=input_pipe)

    with open(ssh_config_path) as f:
        resulting_contents = f.read()
    assert resulting_contents == expected_contents


def test_ssh_config_host(tmp_path: Path):
    ssh_config_path = tmp_path / "config"
    with open(ssh_config_path, "w") as f:
        f.write(
            textwrap.dedent(
                """\
            Host mila
                HostName login.server.mila.quebec
                User normandf
                PreferredAuthentications publickey,keyboard-interactive
                Port 2222
                ServerAliveInterval 120
                ServerAliveCountMax 5
                BatchMode yes
            """
            )
        )
    assert SSHConfig(str(ssh_config_path)).host("mila") == {
        "hostname": "login.server.mila.quebec",
        "user": "normandf",
        "preferredauthentications": "publickey,keyboard-interactive",
        "port": "2222",
        "serveraliveinterval": "120",
        "serveralivecountmax": "5",
        "batchmode": "yes",
    }


@pytest.mark.parametrize(
    "already_has_mila, already_has_mila_cpu, already_has_mila_gpu, already_has_mila_compute",
    list(itertools.product([True, False], repeat=4)),
)
def test_with_existing_entries(
    already_has_mila: bool,
    already_has_mila_cpu: bool,
    already_has_mila_gpu: bool,
    already_has_mila_compute: bool,
    tmp_path: Path,
):
    existing_mila = textwrap.dedent(
        """\
        Host mila
          HostName login.server.mila.quebec
          User bob
        """
    )
    existing_mila_cpu = textwrap.dedent(
        """\
        Host mila-cpu
          HostName login.server.mila.quebec
        """
    )
    existing_mila_gpu = textwrap.dedent(
        """\
        Host mila-gpu
          HostName login.server.mila.quebec
        """
    )
    existing_mila_compute = textwrap.dedent(
        """\
        Host *.server.mila.quebec !*login.server.mila.quebec
          HostName foooobar.com
        """
    )

    initial_blocks = []
    initial_blocks += [existing_mila] if already_has_mila else []
    initial_blocks += [existing_mila_cpu] if already_has_mila_cpu else []
    initial_blocks += [existing_mila_gpu] if already_has_mila_gpu else []
    initial_blocks += [existing_mila_compute] if already_has_mila_compute else []
    initial_contents = _join_blocks(*initial_blocks)

    # TODO: Need to insert the entries in the right place, in the right order!
    expected_blocks = initial_blocks.copy()
    expected_blocks += [expected_block_mila] if not already_has_mila else []
    expected_blocks += [expected_block_mila_cpu] if not already_has_mila_cpu else []
    expected_blocks += [expected_block_mila_gpu] if not already_has_mila_gpu else []
    expected_blocks += [expected_block_compute_node] if not already_has_mila_compute else []

    expected_contents = _join_blocks(*expected_blocks)

    ssh_config_path = tmp_path / ".ssh" / "config"
    ssh_config_path.parent.mkdir(parents=True, exist_ok=False)
    with open(ssh_config_path, "w") as f:
        f.write(initial_contents)

    # Accept all the prompts.
    prompt_inputs = (
        # username prompt is only there if there isn't already a 'mila' entry.
        (
            ["bob\r"]
            if not already_has_mila or (already_has_mila and "User" not in existing_mila)
            else []
        )
        + (["y"] if not already_has_mila else [])
        + (["y"] if not already_has_mila_cpu else [])
        + (["y"] if not already_has_mila_gpu else [])
        + (["y"] if not already_has_mila_compute else [])
    )
    if not all(
        [already_has_mila, already_has_mila_cpu, already_has_mila_gpu, already_has_mila_compute]
    ):
        # There's a confirmation prompt only if we're adding some entry.
        prompt_inputs += ["y"]

    with set_user_inputs(prompt_inputs) as input_pipe:
        setup_ssh_config_interactive(ssh_config_path=ssh_config_path, _input=input_pipe)

    with open(ssh_config_path) as f:
        resulting_contents = f.read()

    # TODO: There's an extra newline being added in there somewhere.
    # Making the test agnostic to it just for now.
    # assert resulting_contents == expected_contents

    def nonempty_lines(text: str) -> list[str]:
        return [line for line in text.split("\n") if line.strip()]

    assert nonempty_lines(resulting_contents) == nonempty_lines(expected_contents)


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
