from __future__ import annotations
from contextlib import contextmanager
import contextlib
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


# TODO: Redesign the prompting mechanism (adjusting the test first).
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
            _join_blocks(
                expected_block_mila,
                expected_block_mila_cpu,
                expected_block_mila_gpu,
                expected_block_compute_node,
            ),
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
            _join_blocks(
                expected_block_mila,
                expected_block_mila_cpu,
                expected_block_mila_gpu,
                expected_block_compute_node,
            ),
        ),
        (
            # Start with an empty file
            "",
            # Enter a username, accept the mila prompt, reject the "confirm" prompt for the mila entry.
            # NOTE: Grouping them into groups of 2 to match (promt_<x>, confirm_prompt_<x>)
            ["bob", *("y", "n")],
            # Shoudn't produce anything.
            "",
        ),
        (
            # Start with an empty file
            "",
            # Enter a username, accept the mila prompt, accept the "confirm" prompt for the mila
            # entry. Reject the next prompt.
            ["bob", *("y", "y"), "n"],
            expected_block_mila.format(user="bob"),
        ),
        (
            # Start with an empty file
            "",
            # Enter a username, accept the mila prompts, accept the first mila-cpu prompt, but
            # reject the confirm prompt for mila-cpu.
            ["bob", *("y", "y"), *("y", "n")],
            expected_block_mila.format(user="bob"),
        ),
        (
            # Start with an empty file
            "",
            # Enter a username, accept the mila prompts, accept the mila-cpu prompts, reject the
            # mila-gpu prompt.
            ["bob", *("y", "y"), *("y", "y"), "n"],
            _join_blocks(
                expected_block_mila,
                expected_block_mila_cpu,
            ),
        ),
        (
            # Start with an empty file
            "",
            # Enter a username, accept the mila prompts, accept the mila-cpu prompts, accept the
            # first mila-gpu prompt, but reject the confirm prompt for mila-gpu.
            ["bob", *("y", "y"), *("y", "y"), *("y", "n")],
            _join_blocks(
                expected_block_mila,
                expected_block_mila_cpu,
            ),
        ),
        (
            # Start with an empty file
            "",
            # Enter a username, accept the mila prompts, accept the mila-cpu prompts, accept the
            # mila-gpu prompts. Reject creating an entry for the compute node access.
            ["bob", *("y", "y"), *("y", "y"), *("y", "y"), "n"],
            _join_blocks(
                expected_block_mila,
                expected_block_mila_cpu,
                expected_block_mila_gpu,
            ),
        ),
        (
            # Start with an empty file
            "",
            # Enter a username, accept the mila prompts, accept the mila-cpu prompts, accept the
            # mila-gpu prompts. Accept creating an entry for the compute node access, but reject
            # the confirm prompt for compute node access.
            ["bob", *("y", "y"), *("y", "y"), *("y", "y"), ("y", "n")],
            _join_blocks(
                expected_block_mila,
                expected_block_mila_cpu,
                expected_block_mila_gpu,
                expected_block_compute_node,
            ),
        ),
        (
            # Start with an empty file
            "",
            # Enter a username, accept the mila prompts, accept the mila-cpu prompts, accept the
            # mila-gpu prompts. Accept the compute node prompts.
            ["bob", *("y", "y"), *("y", "y"), *("y", "y"), ("y", "y")],
            _join_blocks(
                expected_block_mila,
                expected_block_mila_cpu,
                expected_block_mila_gpu,
                expected_block_compute_node,
            ),
        ),
        (
            # Start with a file with the overly general *.server.mila.quebec entry.
            """\
            Host *.server.mila.quebec
              HostName %h
              User bob
              ProxyJump mila
            """,
            # Enter a username, accept the mila prompts, accept the mila-cpu prompts, accept the
            # mila-gpu prompts. REJECT fixing the *.server.mila.quebec entry
            ["bob", *("y", "y"), *("y", "y"), *("y", "y"), "n"],
            _join_blocks(
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
            ),
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
            # NOTE: In this case, it shouldn't also ask for the compute node entry.
            ["bob", *("y", "y"), *("y", "y"), *("y", "y"), "y"],
            _join_blocks(
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
            ),
        ),
        (
            # Start with a non-empty empty file,
            """\
            # a comment in a fake ssh config file
            """,
            # enter user and accept all the prompts after that.
            ["bob", "y", ...],
            _join_blocks(
                "# a comment in a fake ssh config file",
                expected_block_mila,
                expected_block_mila_cpu,
                expected_block_mila_gpu,
                expected_block_compute_node,
            ),
        ),
    ],
    ids=lambda val: textwrap.shorten(textwrap.dedent(val), width=80)
    if isinstance(val, str)
    else textwrap.shorten(str(val), width=30),
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

    ssh_config_path = tmp_path / ".ssh" / "config"
    ssh_config_path.parent.mkdir(parents=True, exist_ok=False)

    if initial_contents is not None:
        with open(ssh_config_path, "w") as f:
            f.write(textwrap.dedent(initial_contents))

    with set_user_inputs(prompt_inputs) as input_pipe:
        with (pytest.raises(SystemExit) if "n" in prompt_inputs else contextlib.nullcontext()):
            setup_ssh_config_interactive(ssh_config_path=ssh_config_path, _input_pipe=input_pipe)
        # except SystemExit as e:
        #     print(e)
        #     pass

    if expected_contents:
        expected_contents = textwrap.dedent(expected_contents)

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
