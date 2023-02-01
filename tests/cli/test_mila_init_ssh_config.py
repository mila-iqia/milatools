from __future__ import annotations

import contextlib
import inspect
import itertools
import textwrap
from functools import partial
from pathlib import Path

import pytest
import questionary
from prompt_toolkit import PromptSession
from prompt_toolkit.input import PipeInput, create_pipe_input
from pytest_regressions.file_regression import FileRegressionFixture

from milatools.cli.commands import setup_ssh_config_interactive
from milatools.cli.utils import SSHConfig


@pytest.fixture
def input_pipe(monkeypatch: pytest.MonkeyPatch):
    """Fixture that creates an input pipe and makes questionary use it.

    To use it, call `input_pipe.send_text("some text")`.

    NOTE: Important: Send the \\r (with one backslash) character if the prompt is on a newline.
    For confirmation prompts, just send one letter, otherwise the '\r' is passed to the next
    prompt, which sees it as just pressing enter, which uses the default value.
    """
    with create_pipe_input() as input_pipe:
        monkeypatch.setattr("questionary.confirm", partial(questionary.confirm, input=input_pipe))
        monkeypatch.setattr("questionary.text", partial(questionary.text, input=input_pipe))
        yield input_pipe


def _join_blocks(*blocks: str, user: str = "bob") -> str:
    return "\n".join(textwrap.dedent(block) for block in blocks).format(user=user)


def _yn(accept: bool):
    return "y" if accept else "n"


def test_creates_ssh_config_file(tmp_path: Path, input_pipe: PipeInput):
    ssh_config_path = tmp_path / "ssh_config"

    for prompt in ["y", "bob\r", "y", "y", "y", "y", "y"]:
        input_pipe.send_text(prompt)
    setup_ssh_config_interactive(tmp_path / "ssh_config")
    assert ssh_config_path.exists()


def parametrize_flags(test_param_names: str):
    flags = ("mila", "mila_cpu", "mila_gpu", "mila_computenode")
    test_params = list(itertools.product([False, True], repeat=4))
    test_accepted_prompt_names: list[list[str]] = [
        sum(
            ([flags[i]] if b else [] for i, b in enumerate(bs)),
            [],
        )
        for bs in test_params
    ]
    test_ids = [
        "-".join(accepted_prompt_names) for accepted_prompt_names in test_accepted_prompt_names
    ]
    return pytest.mark.parametrize(
        test_param_names,
        test_params,
        ids=test_ids,
    )


@pytest.mark.parametrize(
    "confirm_changes",
    [False, True],
    ids=["reject_changes", "confirm_changes"],
)
@parametrize_flags("accept_mila, accept_mila_cpu, accept_mila_gpu, accept_mila_computenode")
@pytest.mark.parametrize(
    "initial_contents",
    [
        "",
        """\
        # A comment in the file.
        """,
        """\
        # a comment
        Host foo
            HostName foobar.com
        """,
        """\
        # a comment
        Host foo
          HostName foobar.com

        # another comment
        """,
    ],
    ids=["empty", "comment_only", "different_indent", "comment_and_entry"],
)
def test_mila_init_no_existing_entries(
    initial_contents: str,
    accept_mila: bool,
    accept_mila_cpu: bool,
    accept_mila_gpu: bool,
    accept_mila_computenode: bool,
    confirm_changes: bool,
    tmp_path: Path,
    file_regression: FileRegressionFixture,
    input_pipe: PipeInput,
):
    """Checks what entries are added to the ssh config file when running the corresponding portion
    of `mila init`.
    """
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
    for prompt in user_inputs:
        input_pipe.send_text(prompt)

    if not any([accept_mila, accept_mila_cpu, accept_mila_gpu, accept_mila_computenode]):
        # Won't get prompted for confirmation if no changes are made.
        should_exit = False
    else:
        should_exit = not confirm_changes

    with contextlib.suppress(SystemExit), (
        pytest.raises(SystemExit) if should_exit else contextlib.nullcontext()
    ):
        setup_ssh_config_interactive(ssh_config_path=ssh_config_path)

    assert ssh_config_path.exists()
    with open(ssh_config_path) as f:
        resulting_contents = f.read()
    file_regression.check(resulting_contents)


def test_questionary_prompts_works_with_input_none():
    """Makes sure that the actual command will work if the _input argument is None, and that it's
    safe for `None` to be passed down all the way down the `questionary` stack as part of **kwargs.
    """
    assert inspect.signature(PromptSession).parameters["input"].default is None


def test_fixes_overly_general_entry(tmp_path: Path, input_pipe: PipeInput):
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
    for user_input in ["bob\r", "n", "n", "n", "y", "y"]:
        input_pipe.send_text(user_input)

    setup_ssh_config_interactive(ssh_config_path=ssh_config_path)

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


@parametrize_flags(
    "already_has_mila, already_has_mila_cpu, already_has_mila_gpu, already_has_mila_compute",
)
def test_with_existing_entries(
    already_has_mila: bool,
    already_has_mila_cpu: bool,
    already_has_mila_gpu: bool,
    already_has_mila_compute: bool,
    file_regression: FileRegressionFixture,
    tmp_path: Path,
    input_pipe: PipeInput,
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

    for prompt_input in prompt_inputs:
        input_pipe.send_text(prompt_input)

    setup_ssh_config_interactive(ssh_config_path=ssh_config_path)

    with open(ssh_config_path) as f:
        resulting_contents = f.read()

    file_regression.check(resulting_contents)
