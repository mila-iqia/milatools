from __future__ import annotations

import contextlib
import subprocess
import textwrap
from functools import partial
from pathlib import Path
from unittest.mock import Mock

import pytest
import questionary
from prompt_toolkit.input import PipeInput, create_pipe_input
from pytest_regressions.file_regression import FileRegressionFixture

from milatools.cli import init_command, local
from milatools.cli.init_command import (
    _get_username,
    _setup_ssh_config_file,
    create_ssh_keypair,
    get_windows_home_path_in_wsl,
    setup_ssh_config,
    setup_windows_ssh_config_from_wsl,
)
from milatools.cli.local import Local
from milatools.cli.utils import SSHConfig, running_inside_WSL


@pytest.fixture
def input_pipe(monkeypatch: pytest.MonkeyPatch):
    """Fixture that creates an input pipe and makes questionary use it.

    To use it, call `input_pipe.send_text("some text")`.

    NOTE: Important: Send the \\r (with one backslash) character if the prompt is on a newline.
    For confirmation prompts, just send one letter, otherwise the '\r' is passed to the next
    prompt, which sees it as just pressing enter, which uses the default value.
    """
    with create_pipe_input() as input_pipe:
        monkeypatch.setattr(
            "questionary.confirm",
            partial(questionary.confirm, input=input_pipe),
        )
        monkeypatch.setattr(
            "questionary.text", partial(questionary.text, input=input_pipe)
        )
        yield input_pipe


def test_questionary_uses_input_pipe(input_pipe: PipeInput):
    """Small test just to make sure that our way of passing the input pipe to Questionary in tests
    makes sense.

    TODO: Ideally we'd want to make sure that the input prompts work exactly the same way in
    our tests as they will for the users, but that's not something I'm confident I can guarantee.
    """
    input_pipe.send_text("bob\r")
    assert questionary.text("name?").unsafe_ask() == "bob"
    input_pipe.send_text("y")
    assert questionary.confirm("confirm?").unsafe_ask() is True
    input_pipe.send_text("n")
    assert questionary.confirm("confirm?").unsafe_ask() is False


def _join_blocks(*blocks: str, user: str = "bob") -> str:
    return "\n".join(textwrap.dedent(block) for block in blocks).format(user=user)


def _yn(accept: bool):
    return "y" if accept else "n"


def test_creates_ssh_config_file(tmp_path: Path, input_pipe: PipeInput):
    ssh_config_path = tmp_path / "ssh_config"

    for prompt in ["y", "bob\r", "y", "y", "y", "y", "y"]:
        input_pipe.send_text(prompt)
    setup_ssh_config(tmp_path / "ssh_config")
    assert ssh_config_path.exists()


@pytest.mark.parametrize(
    "confirm_changes",
    [False, True],
    ids=["reject_changes", "confirm_changes"],
)
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
        """\
        # a comment

        Host foo
          HostName foobar.com




        # another comment after lots of empty lines.
        """,
    ],
    ids=[
        "empty",
        "has_comment",
        "has_different_indent",
        "has_comment_and_entry",
        "has_comment_and_entry_with_extra_space",
    ],
)
def test_setup_ssh(
    initial_contents: str,
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
        _yn(confirm_changes),
    ]
    for prompt in user_inputs:
        input_pipe.send_text(prompt)

    should_exit = not confirm_changes

    with pytest.raises(SystemExit) if should_exit else contextlib.nullcontext():
        setup_ssh_config(ssh_config_path=ssh_config_path)

    assert ssh_config_path.exists()
    with open(ssh_config_path) as f:
        resulting_contents = f.read()

    expected_text = "\n".join(
        [
            "Running the `mila init` command with "
            + (
                "\n".join(
                    [
                        "this initial content:",
                        "",
                        "```",
                        initial_contents,
                        "```",
                    ]
                )
                if initial_contents
                else "no initial ssh config file"
            ),
            "",
            f"and these user inputs: {user_inputs}",
            "leads the following ssh config file:",
            "",
            "```",
            resulting_contents,
            "```",
        ]
    )

    file_regression.check(expected_text, extension=".md")


def test_fixes_overly_general_entry(
    tmp_path: Path,
    input_pipe: PipeInput,
    file_regression: FileRegressionFixture,
):
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

    # Enter username, accept fixing that entry, then confirm.
    for user_input in ["bob\r", "y", "y"]:
        input_pipe.send_text(user_input)

    setup_ssh_config(ssh_config_path=ssh_config_path)

    with open(ssh_config_path) as f:
        resulting_contents = f.read()

    file_regression.check(resulting_contents)
    assert (
        "Host *.server.mila.quebec !*login.server.mila.quebec"
        in resulting_contents.splitlines()
    )


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
    "already_has_mila", [True, False], ids=["has_mila_entry", "no_mila_entry"]
)
@pytest.mark.parametrize(
    "already_has_mila_cpu",
    [True, False],
    ids=["has_mila_cpu_entry", "no_mila_cpu_entry"],
)
@pytest.mark.parametrize(
    "already_has_mila_compute",
    [True, False],
    ids=["has_mila_compute_entry", "no_mila_compute_entry"],
)
def test_with_existing_entries(
    already_has_mila: bool,
    already_has_mila_cpu: bool,
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
    existing_mila_compute = textwrap.dedent(
        """\
        Host *.server.mila.quebec !*login.server.mila.quebec
          HostName foooobar.com
        """
    )

    initial_blocks = []
    initial_blocks += [existing_mila] if already_has_mila else []
    initial_blocks += [existing_mila_cpu] if already_has_mila_cpu else []
    initial_blocks += [existing_mila_compute] if already_has_mila_compute else []
    initial_contents = _join_blocks(*initial_blocks)

    # TODO: Need to insert the entries in the right place, in the right order!

    ssh_config_path = tmp_path / ".ssh" / "config"
    ssh_config_path.parent.mkdir(parents=True, exist_ok=False)
    with open(ssh_config_path, "w") as f:
        f.write(initial_contents)

    # Accept all the prompts.
    username_input = (
        ["bob\r"]
        if not already_has_mila or (already_has_mila and "User" not in existing_mila)
        else []
    )

    controlmaster_block = "\n".join(
        [
            "  ControlMaster auto",
            "  ControlPath ~/.cache/ssh/%r@%h:%p",
            "  ControlPersist 600",
        ]
    )
    if not all(
        [
            already_has_mila and controlmaster_block in existing_mila,
            already_has_mila_cpu,
            already_has_mila_compute and controlmaster_block in existing_mila_compute,
        ]
    ):
        # There's a confirmation prompt only if we're adding some entry.
        confirm_inputs = ["y"]
    else:
        confirm_inputs = []

    prompt_inputs = username_input + confirm_inputs

    for prompt_input in prompt_inputs:
        input_pipe.send_text(prompt_input)

    setup_ssh_config(ssh_config_path=ssh_config_path)

    with open(ssh_config_path) as f:
        resulting_contents = f.read()

    expected_text = "\n".join(
        [
            "Running the `mila init` command with "
            + (
                "\n".join(
                    [
                        "this initial content:",
                        "",
                        "```",
                        initial_contents,
                        "```",
                    ]
                )
                if initial_contents
                else "no initial ssh config file"
            ),
            "",
            f"and these user inputs: {prompt_inputs}",
            "leads to the following ssh config file:",
            "",
            "```",
            resulting_contents,
            "```",
        ]
    )
    file_regression.check(
        expected_text,
        extension=".md",
    )


@pytest.mark.parametrize(
    ("contents", "prompt_inputs", "expected"),
    [
        pytest.param(
            "",  # empty file.
            ["bob\r"],  # enter "bob" then enter.
            "bob",  # get "bob" as username.
            id="empty_file",
        ),
        pytest.param(
            textwrap.dedent(
                """\
                Host mila
                  HostName login.server.mila.quebec
                  User bob
                """
            ),
            [],
            "bob",
            id="existing_mila_entry",
        ),
        pytest.param(
            textwrap.dedent(
                """\
                Host mila
                    HostName login.server.mila.quebec
                """
            ),
            ["bob\r"],
            "bob",
            id="entry_without_user",
        ),
        pytest.param(
            textwrap.dedent(
                """\
                Host mila
                    HostName login.server.mila.quebec
                    User george
                # duplicate entry
                Host mila mila_alias
                    User Bob
                """
            ),
            ["bob\r"],
            "bob",
            id="two_matching_entries",
        ),
        pytest.param(
            textwrap.dedent(
                """\
                Host fooo mila bar baz
                    HostName login.server.mila.quebec
                    User george
                """
            ),
            [],
            "george",
            id="with_aliases",
        ),
        pytest.param(
            "",
            [" \r", "bob\r"],
            "bob",
            id="empty_username",
        ),
    ],
)
def test_get_username(
    contents: str,
    prompt_inputs: list[str],
    expected: str,
    input_pipe: PipeInput,
    tmp_path: Path,
):
    # TODO: We should probably also have a test that checks that keyboard interrupts work.
    # Seems like the text to send for that would be "\x03".
    ssh_config_path = tmp_path / "config"
    with open(ssh_config_path, "w") as f:
        f.write(contents)
    ssh_config = SSHConfig(ssh_config_path)
    if not prompt_inputs:
        input_pipe.close()
    for prompt_input in prompt_inputs:
        input_pipe.send_text(prompt_input)
    assert _get_username(ssh_config) == expected


class TestSetupSshFile:
    def test_create_file(self, tmp_path: Path, input_pipe: PipeInput):
        config_path = tmp_path / "config"
        input_pipe.send_text("y")
        file = _setup_ssh_config_file(config_path)
        assert file.exists()
        assert file.stat().st_mode & 0o777 == 0o600

    def test_refuse_creating_file(self, tmp_path: Path, input_pipe: PipeInput):
        config_path = tmp_path / "config"
        input_pipe.send_text("n")
        with pytest.raises(SystemExit):
            config_path = _setup_ssh_config_file(config_path)
        assert not config_path.exists()

    def test_fix_file_permissions(self, tmp_path: Path):
        config_path = tmp_path / "config"
        config_path.touch(mode=0o644)
        assert config_path.stat().st_mode & 0o777 == 0o644

        # todo: Do we want to have a prompt in this case here?
        # idea: might be nice to also test that the right output is printed
        file = _setup_ssh_config_file(config_path)
        assert file.exists()
        assert file.stat().st_mode & 0o777 == 0o600

    def test_creates_dir(self, tmp_path: Path, input_pipe: PipeInput):
        config_path = tmp_path / "fake_ssh" / "config"
        input_pipe.send_text("y")
        file = _setup_ssh_config_file(config_path)
        assert file.parent.exists()
        assert file.parent.stat().st_mode & 0o777 == 0o700
        assert file.exists()
        assert file.stat().st_mode & 0o777 == 0o600

    @pytest.mark.parametrize("file_exists", [True, False])
    def test_fixes_dir_permission_issues(
        self, file_exists: bool, tmp_path: Path, input_pipe: PipeInput
    ):
        config_path = tmp_path / "fake_ssh" / "config"
        config_path.parent.mkdir(mode=0o755)
        if file_exists:
            config_path.touch(mode=0o600)
        else:
            input_pipe.send_text("y")
        file = _setup_ssh_config_file(config_path)
        assert file.parent.exists()
        assert file.parent.stat().st_mode & 0o777 == 0o700
        assert file.exists()
        assert file.stat().st_mode & 0o777 == 0o600


def test_create_ssh_keypair(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    here = Local()
    mock_run = Mock(
        wraps=subprocess.run,
    )
    monkeypatch.setattr(subprocess, "run", mock_run)
    fake_ssh_folder = tmp_path / "fake_ssh"
    fake_ssh_folder.mkdir(mode=0o700)
    ssh_private_key_path = fake_ssh_folder / "bob"
    create_ssh_keypair(ssh_private_key_path=ssh_private_key_path, local=here)
    mock_run.assert_called_once_with(
        ("ssh-keygen", "-f", str(ssh_private_key_path), "-t", "rsa", "-N=''"),
        universal_newlines=True,
    )
    assert ssh_private_key_path.exists()
    assert ssh_private_key_path.stat().st_mode & 0o777 == 0o600
    ssh_public_key_path = ssh_private_key_path.with_suffix(".pub")
    assert ssh_public_key_path.exists()
    assert ssh_public_key_path.stat().st_mode & 0o777 == 0o644


@pytest.fixture
def linux_ssh_config(
    tmp_path: Path, input_pipe: PipeInput, monkeypatch: pytest.MonkeyPatch
) -> SSHConfig:
    """Creates the SSH config that would be generated by `mila init`."""
    # Enter username, accept fixing that entry, then confirm.
    ssh_config_path = tmp_path / "ssh_config"

    for prompt in ["y", "bob\r", "y"]:
        input_pipe.send_text(prompt)

    monkeypatch.setattr("sys.platform", "linux")
    # TODO: The config will be different if we run the tests on Windows, it won't
    # contain the ControlMaster entries.
    setup_ssh_config(ssh_config_path)

    return SSHConfig(ssh_config_path)


@pytest.mark.parametrize("accept_changes", [True, False], ids=["accept", "reject"])
def test_setup_windows_ssh_config_from_wsl(
    tmp_path: Path,
    linux_ssh_config: SSHConfig,
    input_pipe: PipeInput,
    file_regression: FileRegressionFixture,
    monkeypatch: pytest.MonkeyPatch,
    accept_changes: bool,
):
    initial_contents = linux_ssh_config.cfg.config()
    windows_home = tmp_path / "fake_windows_home"
    windows_home.mkdir(exist_ok=False)
    windows_ssh_config_path = windows_home / ".ssh" / "config"

    monkeypatch.setattr(
        init_command,
        running_inside_WSL.__name__,
        Mock(spec=running_inside_WSL, return_value=True),
    )
    monkeypatch.setattr(
        init_command,
        get_windows_home_path_in_wsl.__name__,
        Mock(spec=get_windows_home_path_in_wsl, return_value=windows_home),
    )
    user_inputs: list[str] = []
    if not windows_ssh_config_path.exists():
        # We accept creating the Windows SSH config file for now.
        user_inputs.append("y")
    user_inputs.append("y" if accept_changes else "n")

    for prompt in user_inputs:
        input_pipe.send_text(prompt)

    setup_windows_ssh_config_from_wsl(linux_ssh_config=linux_ssh_config)

    assert windows_ssh_config_path.exists()
    assert windows_ssh_config_path.stat().st_mode & 0o777 == 0o600
    assert windows_ssh_config_path.parent.stat().st_mode & 0o777 == 0o700
    if not accept_changes:
        assert windows_ssh_config_path.read_text() == ""

    expected_text = "\n".join(
        [
            "When this SSH config is already present in the WSL environment with "
            + (
                "\n".join(
                    [
                        "these initial contents:",
                        "```",
                        initial_contents,
                        "```",
                        "",
                    ]
                )
                if initial_contents.strip()
                else "no initial ssh config file"
            ),
            "",
            f"and these user inputs: {tuple(user_inputs)}",
            "leads the following ssh config file on the Windows side:",
            "",
            "```",
            windows_ssh_config_path.read_text(),
            "```",
        ]
    )

    file_regression.check(expected_text, extension=".md")


def test_setup_windows_ssh_config_from_wsl_copies_keys(
    tmp_path: Path,
    linux_ssh_config: SSHConfig,
    input_pipe: PipeInput,
    monkeypatch: pytest.MonkeyPatch,
):
    linux_home = tmp_path / "fake_linux_home"
    linux_home.mkdir(exist_ok=False)
    windows_home = tmp_path / "fake_windows_home"
    windows_home.mkdir(exist_ok=False)
    monkeypatch.setattr(Path, "home", Mock(spec=Path.home, return_value=linux_home))

    monkeypatch.setattr(
        init_command,
        running_inside_WSL.__name__,
        Mock(spec=running_inside_WSL, return_value=True),
    )
    monkeypatch.setattr(
        init_command,
        get_windows_home_path_in_wsl.__name__,
        Mock(spec=get_windows_home_path_in_wsl, return_value=windows_home),
    )

    fake_linux_ssh_dir = linux_home / ".ssh"
    fake_linux_ssh_dir.mkdir(mode=0o700)

    private_key_text = "THIS IS A PRIVATE KEY"
    linux_private_key_path = fake_linux_ssh_dir / "id_rsa"
    linux_private_key_path.write_text(private_key_text)

    public_key_text = "THIS IS A PUBLIC KEY"
    linux_public_key_path = linux_private_key_path.with_suffix(".pub")
    linux_public_key_path.write_text(public_key_text)

    input_pipe.send_text("y")  # accept creating the Windows config file
    input_pipe.send_text("y")  # accept the changes

    setup_windows_ssh_config_from_wsl(linux_ssh_config=linux_ssh_config)

    windows_private_key_path = windows_home / ".ssh" / "id_rsa"
    windows_public_key_path = windows_private_key_path.with_suffix(".pub")

    assert windows_private_key_path.exists()
    assert windows_private_key_path.read_text() == private_key_text
    assert windows_public_key_path.exists()
    assert windows_public_key_path.read_text() == public_key_text
