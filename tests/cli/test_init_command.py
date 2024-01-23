from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import textwrap
from functools import partial
from logging import getLogger as get_logger
from pathlib import Path
from unittest.mock import Mock

import pytest
import pytest_mock
import questionary
from prompt_toolkit.input import PipeInput, create_pipe_input
from pytest_regressions.file_regression import FileRegressionFixture

from milatools.cli import init_command
from milatools.cli.init_command import (
    DRAC_CLUSTERS,
    _get_drac_username,
    _get_mila_username,
    _setup_ssh_config_file,
    create_ssh_keypair,
    get_windows_home_path_in_wsl,
    setup_passwordless_ssh_access,
    setup_passwordless_ssh_access_to_cluster,
    setup_ssh_config,
    setup_vscode_settings,
    setup_windows_ssh_config_from_wsl,
)
from milatools.cli.local import Local, check_passwordless
from milatools.cli.utils import SSHConfig, running_inside_WSL

from .common import (
    in_github_CI,
    on_windows,
    passwordless_ssh_connection_to_localhost_is_setup,
    xfails_on_windows,
)

logger = get_logger(__name__)


def raises_NoConsoleScreenBufferError_on_windows_ci_action():
    if sys.platform == "win32":
        import prompt_toolkit.output.win32

        raises = prompt_toolkit.output.win32.NoConsoleScreenBufferError
    else:
        raises = ()

    return xfails_on_windows(
        raises=raises,
        reason="TODO: Tests using input pipes don't work on GitHub CI.",
        strict=False,
    )


def permission_bits_check_doesnt_work_on_windows():
    return pytest.mark.xfail(
        sys.platform == "win32",
        raises=AssertionError,
        reason="TODO: The check for permission bits is failing on Windows in CI.",
    )


# Set a module-level mark: Each test cannot take longer than 1 second to run.
pytestmark = pytest.mark.timeout(10)


@pytest.fixture
def input_pipe(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest):
    """Fixture that creates an input pipe and makes questionary use it.

    To use it, call `input_pipe.send_text("some text")`.

    NOTE: Important: Send the \\r (with one backslash) character if the prompt is on a
    newline.
    For confirmation prompts, just send one letter, otherwise the '\r' is passed to the
    next prompt, which sees it as just pressing enter, which uses the default value.
    """
    request.node.add_marker(raises_NoConsoleScreenBufferError_on_windows_ci_action())
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
    """Small test just to make sure that our way of passing the input pipe to
    Questionary in tests makes sense.

    TODO: Ideally we'd want to make sure that the input prompts work exactly the same
    way in our tests as they will for the users, but that's not something I'm confident
    I can guarantee.
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

    for prompt in [
        "y",
        "bob\r",  # mila username
        "y",  # drac?
        "bob\r",  # drac username
        "y",
        "y",
        "y",
        "y",
        "y",
    ]:
        input_pipe.send_text(prompt)
    setup_ssh_config(tmp_path / "ssh_config")
    assert ssh_config_path.exists()


@pytest.mark.parametrize(
    "drac_username",
    [None, "bob"],
    ids=["no_drac", "drac"],
)
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
    drac_username: str | None,
    tmp_path: Path,
    file_regression: FileRegressionFixture,
    input_pipe: PipeInput,
):
    """Checks what entries are added to the ssh config file when running the
    corresponding portion of `mila init`."""
    ssh_config_path = tmp_path / ".ssh" / "config"
    ssh_config_path.parent.mkdir(parents=True, exist_ok=False)

    if initial_contents:
        initial_contents = textwrap.dedent(initial_contents)

    if initial_contents is not None:
        with open(ssh_config_path, "w") as f:
            f.write(initial_contents)

    user_inputs = [
        "bob\r",  # username on Mila cluster
        *(  # DRAC account? + enter username
            ["n"] if drac_username is None else ["y", drac_username + "\r"]
        ),
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
            f"and these user inputs: {tuple(user_inputs)}",
            "leads the following ssh config file:",
            "",
            "```",
            resulting_contents,
            "```",
            "",
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
    for user_input in [
        "bob\r",  # mila username
        "n",  # DRAC account?
        "y",
        "y",
    ]:
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
    "already_has_drac", [True, False], ids=["has_drac_entries", "no_drac_entries"]
)
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
    already_has_drac: bool,
    file_regression: FileRegressionFixture,
    tmp_path: Path,
    input_pipe: PipeInput,
):
    user = "bob"
    existing_mila = textwrap.dedent(
        f"""\
        Host mila
          HostName login.server.mila.quebec
          User {user}
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
    existing_drac = textwrap.dedent(
        f"""
        # Compute Canada
        Host beluga cedar graham narval niagara
          Hostname %h.alliancecan.ca
          User {user}
        Host mist
          Hostname mist.scinet.utoronto.ca
          User {user}
        Host !beluga  bc????? bg????? bl?????
          ProxyJump beluga
          User {user}
        Host !cedar   cdr? cdr?? cdr??? cdr????
          ProxyJump cedar
          User {user}
        Host !graham  gra??? gra????
          ProxyJump graham
          User {user}
        Host !narval  nc????? ng?????
          ProxyJump narval
          User {user}
        Host !niagara nia????
          ProxyJump niagara
          User {user}
        """
    )

    initial_blocks = []
    initial_blocks += [existing_mila] if already_has_mila else []
    initial_blocks += [existing_mila_cpu] if already_has_mila_cpu else []
    initial_blocks += [existing_mila_compute] if already_has_mila_compute else []
    initial_blocks += [existing_drac] if already_has_drac else []
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
            already_has_drac,
        ]
    ):
        # There's a confirmation prompt only if we're adding some entry.
        confirm_inputs = ["y"]
    else:
        confirm_inputs = []

    drac_username_inputs = []
    if not already_has_drac:
        drac_username_inputs = ["y", f"{user}\r"]
    prompt_inputs = username_input + drac_username_inputs + confirm_inputs

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
    # TODO: We should probably also have a test that checks that keyboard interrupts
    # work.
    # Seems like the text to send for that would be "\x03".
    ssh_config_path = tmp_path / "config"
    with open(ssh_config_path, "w") as f:
        f.write(contents)
    ssh_config = SSHConfig(ssh_config_path)
    if not prompt_inputs:
        input_pipe.close()
    for prompt_input in prompt_inputs:
        input_pipe.send_text(prompt_input)
    assert _get_mila_username(ssh_config) == expected


@pytest.mark.parametrize(
    ("contents", "prompt_inputs", "expected"),
    [
        pytest.param(
            "",  # empty file.
            ["n"],  # No I don't have a DRAC account.
            None,  # get None as a result
            id="no_drac_account",
        ),
        pytest.param(
            "",  # empty file.
            ["y", "bob\r"],  # enter yes, then "bob" then enter.
            "bob",  # get "bob" as username.
            id="empty_file",
        ),
        pytest.param(
            textwrap.dedent(
                """\
                Host narval
                  HostName narval.computecanada.ca
                  User bob
                """
            ),
            [],
            "bob",
            id="existing_drac_entry",
        ),
        pytest.param(
            textwrap.dedent(
                """\
                Host beluga cedar graham narval niagara
                  HostName %h.computecanada.ca
                  ControlMaster auto
                  ControlPath ~/.cache/ssh/%r@%h:%p
                  ControlPersist 600
                """
            ),
            ["y", "bob\r"],  # Yes I have a username on the drac clusters, and it's bob.
            "bob",
            id="entry_without_user",
        ),
        pytest.param(
            textwrap.dedent(
                """\
                Host beluga cedar graham narval niagara
                    HostName login.server.mila.quebec
                    User george
                # duplicate entry
                Host beluga cedar graham narval niagara other_cluster
                    User Bob
                """
            ),
            ["y", "bob\r"],
            "bob",
            id="two_matching_entries",
        ),
        pytest.param(
            textwrap.dedent(
                """\
                Host fooo beluga bar baz
                    HostName beluga.alliancecan.ca
                    User george
                """
            ),
            [],
            "george",
            id="with_aliases",
        ),
        pytest.param(
            "",
            # Yes (by pressing just enter), then an invalid username (space), then a
            # real username.
            ["\r", " \r", "bob\r"],
            "bob",
            id="empty_username",
        ),
    ],
)
def test_get_drac_username(
    contents: str,
    prompt_inputs: list[str],
    expected: str | None,
    input_pipe: PipeInput,
    tmp_path: Path,
):
    ssh_config_path = tmp_path / "config"
    with open(ssh_config_path, "w") as f:
        f.write(contents)
    ssh_config = SSHConfig(ssh_config_path)
    if not prompt_inputs:
        input_pipe.close()
    for prompt_input in prompt_inputs:
        input_pipe.send_text(prompt_input)
    assert _get_drac_username(ssh_config) == expected


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

    @permission_bits_check_doesnt_work_on_windows()
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

    @pytest.mark.parametrize(
        "file_exists",
        [
            pytest.param(
                True,
                marks=permission_bits_check_doesnt_work_on_windows(),
            ),
            False,
        ],
    )
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


# takes a little longer in the CI runner (Windows in particular)
@pytest.mark.timeout(10)
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

    mock_run.assert_called_once()
    assert ssh_private_key_path.exists()
    if not on_windows:
        assert ssh_private_key_path.stat().st_mode & 0o777 == 0o600
    ssh_public_key_path = ssh_private_key_path.with_suffix(".pub")
    assert ssh_public_key_path.exists()
    if not on_windows:
        assert ssh_public_key_path.stat().st_mode & 0o777 == 0o644


@pytest.fixture
def linux_ssh_config(
    tmp_path: Path, input_pipe: PipeInput, monkeypatch: pytest.MonkeyPatch
) -> SSHConfig:
    """Creates the SSH config that would be generated by `mila init`."""
    # Enter username, accept fixing that entry, then confirm.
    ssh_config_path = tmp_path / "ssh_config"

    for prompt in [
        "y",  # Create an ssh config file?
        "bob\r",  # What's your username on the Mila cluster?
        "y",  # Do you also have a DRAC account?
        "bob\r",  # username on DRAC
        "y",  # accept adding the entries in the ssh config
    ]:
        input_pipe.send_text(prompt)

    if sys.platform.startswith("win"):
        pytest.skip(
            "TODO: Issue when changing sys.platform to get the Linux config when "
            "on Windows."
        )
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


@xfails_on_windows(
    raises=AssertionError, reason="TODO: buggy test: getting assert None is not None."
)
@pytest.mark.parametrize(
    "initial_settings", [None, {}, {"foo": "bar"}, {"remote.SSH.connectTimeout": 123}]
)
@pytest.mark.parametrize("accept_changes", [True, False], ids=["accept", "reject"])
def test_setup_vscode_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    input_pipe: PipeInput,
    initial_settings: dict | None,
    file_regression: FileRegressionFixture,
    accept_changes: bool,
):
    vscode_settings_json_path = tmp_path / "settings.json"
    if initial_settings is not None:
        with open(vscode_settings_json_path, "w") as f:
            json.dump(initial_settings, f, indent=4)

    monkeypatch.setattr(
        init_command,
        init_command.vscode_installed.__name__,
        Mock(spec=init_command.vscode_installed, return_value=True),
    )
    monkeypatch.setattr(
        init_command,
        init_command.get_expected_vscode_settings_json_path.__name__,
        Mock(
            spec=init_command.get_expected_vscode_settings_json_path,
            return_value=vscode_settings_json_path,
        ),
    )

    user_inputs = ["y" if accept_changes else "n"]
    for user_input in user_inputs:
        input_pipe.send_text(user_input)

    setup_vscode_settings()

    resulting_contents: str | None = None
    resulting_settings: dict | None = None

    if not accept_changes and initial_settings is None:
        # Shouldn't create the file if we don't accept the changes and there's no
        # initial file.
        assert not vscode_settings_json_path.exists()

    if vscode_settings_json_path.exists():
        resulting_contents = vscode_settings_json_path.read_text()
        resulting_settings = json.loads(resulting_contents)
        assert isinstance(resulting_settings, dict)

    if not accept_changes:
        if initial_settings is None:
            assert not vscode_settings_json_path.exists()
            return  # skip creating the regression file in that case.
        assert resulting_settings == initial_settings
        return

    assert resulting_contents is not None
    assert resulting_settings is not None

    expected_text = "\n".join(
        [
            f"Calling `{setup_vscode_settings.__name__}()` with "
            + (
                "\n".join(
                    [
                        "this initial content:",
                        "",
                        "```json",
                        json.dumps(initial_settings, indent=4),
                        "```",
                    ]
                )
                if initial_settings is not None
                else "no initial VsCode settings file"
            ),
            "",
            f"and these user inputs: {tuple(user_inputs)}",
            "leads the following VsCode settings file:",
            "",
            "```json",
            resulting_contents,
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


BACKUP_SSH_DIR = Path.home() / ".ssh_backup"
USE_MY_REAL_SSH_DIR = os.environ.get("USE_MY_REAL_SSH_DIR", "0") == "1"
"""Set this to `True` for the tests below to actually use your real SSH directory.

A backup is saved in `BACKUP_SSH_DIR`.
"""


@pytest.fixture
def backup_ssh_dir():
    """Creates a backup of the SSH config files."""
    import shutil

    assert in_github_CI or USE_MY_REAL_SSH_DIR

    ssh_dir = Path.home() / ".ssh"
    backup_ssh_dir = BACKUP_SSH_DIR

    ssh_dir_existed_before = ssh_dir.exists()

    def _ignore_sockets_dir(src: str, names: list[str]) -> list[str]:
        return names if Path(src).name == "sockets" else []

    if ssh_dir_existed_before:
        logger.warning(f"Backing up {ssh_dir} to {backup_ssh_dir}")
        if backup_ssh_dir.exists():
            shutil.rmtree(backup_ssh_dir)
        shutil.copytree(ssh_dir, backup_ssh_dir, ignore=_ignore_sockets_dir)
    else:
        logger.warning(f"Test might temporarily create files in a new {ssh_dir} dir.")

    yield backup_ssh_dir

    if ssh_dir_existed_before:
        logger.warning(f"Restoring {ssh_dir} from backup at {backup_ssh_dir}")
        if ssh_dir.exists():
            shutil.rmtree(ssh_dir)
        shutil.copytree(backup_ssh_dir, ssh_dir)
        shutil.rmtree(backup_ssh_dir)
    else:
        logger.warning(f"Removing temporarily generated sshdir at {ssh_dir}.")
        if ssh_dir.exists():
            shutil.rmtree(ssh_dir)


@pytest.mark.skipif(
    not (
        (in_github_CI or USE_MY_REAL_SSH_DIR)
        and passwordless_ssh_connection_to_localhost_is_setup
    ),
    reason=(
        "It's a bit risky to actually change the SSH config directory on a dev "
        "machine. Only doing it in the CI or if the USE_MY_REAL_SSH_DIR env var is set."
    ),
)
@pytest.mark.timeout(20)
@pytest.mark.parametrize(
    "passwordless_to_cluster_is_already_setup",
    [True, False],
    ids=["already_setup", "not_already_setup"],
)
@pytest.mark.parametrize(
    "user_accepts_registering_key",
    [True, False],
    ids=["accept_registering_key", "reject_registering_key"],
)
def test_setup_passwordless_ssh_access_to_cluster(
    input_pipe: PipeInput,
    backup_ssh_dir: Path,
    mocker: pytest_mock.MockerFixture,
    passwordless_to_cluster_is_already_setup: bool,
    user_accepts_registering_key: bool,
):
    """Test the function that sets up passwordless SSH to a cluster (localhost in tests)

    NOTE: Running this test will make a backup of the ~/.ssh directory in
    `backup_ssh_dir` (~/.ssh_backup), and restore it after the test.
    For that reason, it currently only runs in the GitHub CI, unless you specifically
    set the `USE_MY_REAL_SSH_DIR` env variable to '1'.
    """
    assert passwordless_ssh_connection_to_localhost_is_setup
    assert in_github_CI or USE_MY_REAL_SSH_DIR

    ssh_dir = Path.home() / ".ssh"
    authorized_keys_file = ssh_dir / "authorized_keys"
    backup_authorized_keys_file = backup_ssh_dir / "authorized_keys"
    assert backup_authorized_keys_file.exists()

    if not passwordless_to_cluster_is_already_setup:
        if authorized_keys_file.exists():
            logger.warning(
                f"Temporarily removing {authorized_keys_file}. "
                f"(A backup is available at {backup_authorized_keys_file})"
            )
            authorized_keys_file.unlink()

        input_pipe.send_text("y" if user_accepts_registering_key else "n")

        assert not check_passwordless("localhost")
    else:
        assert check_passwordless("localhost")

    logger.info(backup_authorized_keys_file.read_text())

    def _mock_subprocess_run(command: tuple[str], *args, **kwargs):
        """Mock of the ssh-copy-id command.

        Copies the `authorized_keys` file from the backup to the (temporarily cleared)
        .ssh directory.
        """
        logger.debug(f"Running: {command} {args} {kwargs}")
        if sys.platform == "linux":
            assert command[0] == "ssh-copy-id"
        ssh_dir.mkdir(exist_ok=True, mode=0o700)
        shutil.copy(backup_authorized_keys_file, authorized_keys_file)
        return subprocess.CompletedProcess(command, 0, "", "")

    mock_subprocess_run = mocker.patch("subprocess.run", wraps=_mock_subprocess_run)

    success = setup_passwordless_ssh_access_to_cluster("localhost")

    if passwordless_to_cluster_is_already_setup:
        mock_subprocess_run.assert_not_called()
        assert success is True
    elif user_accepts_registering_key:
        mock_subprocess_run.assert_called_once()
        assert success is True
    else:
        mock_subprocess_run.assert_not_called()
        assert success is False


@pytest.mark.timeout(10)
@pytest.mark.skipif(
    not (in_github_CI or USE_MY_REAL_SSH_DIR),
    reason=(
        "It's a bit risky to actually change the SSH config directory on a dev "
        "machine. Only doing it in the CI or if the USE_MY_REAL_SSH_DIR env var is set."
    ),
)
@pytest.mark.parametrize(
    "drac_clusters_in_ssh_config",
    [[]] + [DRAC_CLUSTERS[i:] for i in range(len(DRAC_CLUSTERS))],
)
@pytest.mark.parametrize(
    "accept_generating_key",
    [True, False],
    ids=["accept_generate_key", "accept_generate_key"],
)
@pytest.mark.parametrize(
    "public_key_exists",
    [True, False],
    ids=["key_exists", "no_key"],
)
@pytest.mark.parametrize(
    "accept_mila", [True, False], ids=["accept_mila", "reject_mila"]
)
@pytest.mark.parametrize(
    "accept_drac", [True, False], ids=["accept_drac", "reject_drac"]
)
def test_setup_passwordless_ssh_access(
    accept_generating_key: bool,
    public_key_exists: bool,
    accept_mila: bool,
    accept_drac: bool,
    drac_clusters_in_ssh_config: list[str],
    # capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    backup_ssh_dir: Path,
    input_pipe: PipeInput,
):
    assert in_github_CI or USE_MY_REAL_SSH_DIR
    ssh_dir = Path.home() / ".ssh"
    if ssh_dir.exists():
        logger.warning(
            f"Temporarily deleting the ssh dir (backed up at {backup_ssh_dir})"
        )
        shutil.rmtree(ssh_dir)

    if not public_key_exists:
        # There should be no ssh keys in the ssh dir before calling the function.
        # We should get a prompt asking if we want to generate a key.
        input_pipe.send_text("y" if accept_generating_key else "n")
    else:
        # There should be an ssh key in the .ssh dir.
        # Won't ask to generate a key.
        create_ssh_keypair(
            ssh_private_key_path=ssh_dir / "id_rsa_milatools", local=Local()
        )
        if drac_clusters_in_ssh_config:
            # We should get a promtp asking if we want or not to register the public key
            # on the DRAC clusters.
            input_pipe.send_text("y" if accept_drac else "n")

    # Pre-populate the ssh config file.
    ssh_config_path = tmp_path / "ssh_config"
    ssh_config_path.write_text(
        textwrap.dedent(
            f"""\
            Host {' '.join(drac_clusters_in_ssh_config)}
                Hostname %h.computecanada.ca
                User bob
            """
        )
        if drac_clusters_in_ssh_config
        else ""
    )
    ssh_config = SSHConfig(path=ssh_config_path)

    # We mock the main function used by the `setup_passwordless_ssh_access` function.
    # It's okay because we have a good test for it above. Therefore we just test how it
    # gets called here.
    mock_setup_passwordless_ssh_access_to_cluster = Mock(
        spec=setup_passwordless_ssh_access_to_cluster,
        side_effect=[accept_mila, *(accept_drac for _ in drac_clusters_in_ssh_config)],
    )
    import milatools.cli.init_command

    monkeypatch.setattr(
        milatools.cli.init_command,
        setup_passwordless_ssh_access_to_cluster.__name__,
        mock_setup_passwordless_ssh_access_to_cluster,
    )

    result = setup_passwordless_ssh_access(ssh_config)

    if not public_key_exists:
        if accept_generating_key:
            assert ssh_dir.exists()
            assert ssh_dir.stat().st_mode & 0o777 == 0o700
            assert (ssh_dir / "id_rsa").exists()
            assert (ssh_dir / "id_rsa").stat().st_mode & 0o777 == 0o600
            assert (ssh_dir / "id_rsa.pub").exists()
        else:
            assert not (ssh_dir / "id_rsa").exists()
            assert not (ssh_dir / "id_rsa.pub").exists()
            assert not result
            mock_setup_passwordless_ssh_access_to_cluster.assert_not_called()
            return

    mock_setup_passwordless_ssh_access_to_cluster.assert_any_call("mila")
    if not accept_mila:
        mock_setup_passwordless_ssh_access_to_cluster.assert_called_once_with("mila")
        assert result is False
        return

    # If we accept to setup passwordless SSH access to the Mila cluster, we go on
    # to ask for DRAC clusters.
    if not drac_clusters_in_ssh_config:
        assert result is True
        mock_setup_passwordless_ssh_access_to_cluster.assert_called_once_with("mila")
        return

    # If there were DRAC clusters in the SSH config, then the fn is called for each
    # cluster, unless the user rejects setting up passwordless SSH access to one of the
    # clusters, in which case there's an early return False.
    if not accept_drac:
        assert len(mock_setup_passwordless_ssh_access_to_cluster.mock_calls) == 2
        assert result is False
        return

    for drac_cluster in drac_clusters_in_ssh_config:
        mock_setup_passwordless_ssh_access_to_cluster.assert_any_call(drac_cluster)
    assert result is True
