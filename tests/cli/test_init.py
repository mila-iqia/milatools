from __future__ import annotations

import contextlib
import errno
import getpass
import json
import os
import shutil
import subprocess
import sys
import textwrap
from functools import partial
from logging import getLogger as get_logger
from pathlib import Path, PosixPath, PurePosixPath
from unittest.mock import Mock

import invoke
import paramiko
import pytest
import pytest_mock
import questionary
from prompt_toolkit.input import PipeInput, create_pipe_input
from pytest_regressions.file_regression import FileRegressionFixture

from milatools.cli import init
from milatools.cli.init import (
    DRAC_CLUSTERS,
    _get_drac_username,
    _get_mila_username,
    _setup_ssh_config_file,
    create_ssh_keypair,
    get_windows_home_path_in_wsl,
    has_passphrase,
    setup_keys_on_login_node,
    setup_passwordless_ssh_access,
    setup_passwordless_ssh_access_to_cluster,
    setup_ssh_config,
    setup_vscode_settings,
    setup_windows_ssh_config_from_wsl,
)
from milatools.cli.utils import (
    SSHConfig,
    running_inside_WSL,
)
from milatools.utils.local_v1 import LocalV1
from milatools.utils.remote_v1 import RemoteV1
from milatools.utils.remote_v2 import (
    SSH_CACHE_DIR,
    SSH_CONFIG_FILE,
    RemoteV2,
    get_controlpath_for,
    is_already_logged_in,
)

from .common import (
    function_call_string,
    in_github_CI,
    in_self_hosted_github_CI,
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
            [],  # user input doesn't matter (won't get asked).
            "george",
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
    ssh_config = paramiko.SSHConfig.from_path(str(ssh_config_path))
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
            [],  # will not get asked for input.
            "george",
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
    ssh_config = paramiko.SSHConfig.from_path(str(ssh_config_path))
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
@pytest.mark.timeout(20)
@pytest.mark.parametrize(
    ("passphrase", "expected"),
    [("", False), ("bobobo", True), ("\n", True), (" ", True)],
)
@pytest.mark.parametrize(
    "filename",
    [
        "bob",
        "dir with spaces/somefile",
        "dir_with_'single_quotes'/somefile",
        pytest.param(
            'dir_with_"doublequotes"/somefile',
            marks=pytest.mark.xfail(
                sys.platform == "win32",
                strict=True,
                raises=OSError,
                reason="Doesn't work on Windows.",
            ),
        ),
        pytest.param(
            "windows_style_dir\\bob",
            marks=pytest.mark.skipif(
                sys.platform != "win32", reason="only runs on Windows."
            ),
        ),
    ],
)
def test_create_ssh_keypair(
    mocker: pytest_mock.MockerFixture,
    tmp_path: Path,
    filename: str,
    passphrase: str,
    expected: bool,
):
    # Wrap the subprocess.run call (but also actually execute the commands).
    subprocess_run = mocker.patch("subprocess.run", wraps=subprocess.run)

    fake_ssh_folder = tmp_path / "fake_ssh"
    fake_ssh_folder.mkdir(mode=0o700)
    ssh_private_key_path = fake_ssh_folder / filename
    ssh_private_key_path.parent.mkdir(mode=0o700, exist_ok=True, parents=True)

    create_ssh_keypair(ssh_private_key_path=ssh_private_key_path, passphrase=passphrase)

    subprocess_run.assert_called_once()
    assert ssh_private_key_path.exists()
    if not on_windows:
        assert ssh_private_key_path.stat().st_mode & 0o777 == 0o600
    ssh_public_key_path = ssh_private_key_path.with_suffix(".pub")
    assert ssh_public_key_path.exists()
    if not on_windows:
        assert ssh_public_key_path.stat().st_mode & 0o777 == 0o644

    assert has_passphrase(ssh_private_key_path) == expected


@pytest.fixture
def linux_ssh_config(
    tmp_path: Path, input_pipe: PipeInput, monkeypatch: pytest.MonkeyPatch
) -> SSHConfig:
    """Creates the SSH config that is generated by `mila init` on a Linux machine."""
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
        init,
        running_inside_WSL.__name__,
        Mock(spec=running_inside_WSL, return_value=True),
    )
    monkeypatch.setattr(
        init,
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

    setup_windows_ssh_config_from_wsl(
        linux_ssh_config_path=PosixPath(linux_ssh_config.path)
    )

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
        init,
        init.vscode_installed.__name__,
        Mock(spec=init.vscode_installed, return_value=True),
    )
    monkeypatch.setattr(
        init,
        init.get_expected_vscode_settings_json_path.__name__,
        Mock(
            spec=init.get_expected_vscode_settings_json_path,
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
        init,
        running_inside_WSL.__name__,
        Mock(spec=running_inside_WSL, return_value=True),
    )
    monkeypatch.setattr(
        init,
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

    setup_windows_ssh_config_from_wsl(
        linux_ssh_config_path=PosixPath(linux_ssh_config.path)
    )

    windows_private_key_path = windows_home / ".ssh" / "id_rsa"
    windows_public_key_path = windows_private_key_path.with_suffix(".pub")

    assert windows_private_key_path.exists()
    assert windows_private_key_path.read_text() == private_key_text
    assert windows_public_key_path.exists()
    assert windows_public_key_path.read_text() == public_key_text


BACKUP_SSH_DIR = Path.home() / ".ssh_backup"
BACKUP_SSH_CACHE_DIR = Path.home() / ".cache/ssh_backup"
# note: Needs to have a different name (in the case where) we're using the
BACKUP_REMOTE_SSH_DIR = ".ssh_backup_remote"
USE_MY_REAL_SSH_DIR = os.environ.get("USE_MY_REAL_SSH_DIR", "0") == "1"
"""Set this to `True` for the tests below to actually use your real SSH directory.

A backup is saved in `BACKUP_SSH_DIR`.
"""


@contextlib.contextmanager
def backup_dir(directory: Path, backup_directory: Path):
    dir_existed_before = directory.exists()

    # make hard links to the files in the backup directory.
    def copy_fn(source, dest):
        source = Path(source)
        dest = Path(dest)
        try:
            return shutil.copy2(source, dest)
        except OSError as err:
            if err.errno != errno.ENXIO:
                raise
            # This error (errno 6) happens when trying to copy a socket file.
            # In this case, we make a hard link in the backup dir.

            os.link(source, dest)
            return dest

    if dir_existed_before:
        logger.warning(f"Backing up {directory} to {backup_directory}")
        if backup_directory.exists():
            # shutil.rmtree(backup_directory)
            raise RuntimeError(
                f"The backup directory {backup_directory} to be used as backup for "
                f"{directory} already exists! "
                f"Refusing to remove it to avoid losing backed up files. "
                f"(Consider manually restoring {directory} from {backup_directory})."
            )
        shutil.copytree(directory, backup_directory, copy_function=copy_fn)
    else:
        logger.warning(f"Test might temporarily create files in {directory}.")
    try:
        yield backup_directory
    finally:
        if dir_existed_before:
            logger.warning(f"Restoring {directory} from backup at {backup_directory}")
            if directory.exists():
                shutil.rmtree(directory)

            shutil.copytree(backup_directory, directory, copy_function=copy_fn)
            shutil.rmtree(backup_directory)
        else:
            logger.warning(f"Removing temporarily generated dir {directory}.")
            if directory.exists():
                shutil.rmtree(directory)


@contextlib.contextmanager
def backup_remote_dir(
    remote: RemoteV2 | RemoteV1,
    directory: PurePosixPath,
    backup_directory: PurePosixPath,
):
    # IDEA: Make the equivalent function, but that backs up a directory on a remote
    # machine.
    def _exists(dir: PurePosixPath) -> bool:
        result = remote.run(f"test -d {dir}", display=True, warn=True, hide=True)
        if isinstance(result, invoke.runners.Result):
            return result.return_code == 0
        return result.returncode == 0

    assert not _exists(PurePosixPath("/does/not/exist"))
    if remote.hostname == "localhost":
        assert _exists(PurePosixPath(Path.cwd()))

    def _rmtree(dir: PurePosixPath):
        return remote.run(f"rm -r {dir}", display=True, hide=False)

    def _copytree(source_dir: PurePosixPath, dest_dir: PurePosixPath):
        remote.run(f"mkdir -p {dest_dir.parent}", display=True)
        return remote.run(f"cp -r {source_dir} {dest_dir}", display=True)

    dir_existed_before = _exists(directory)

    if dir_existed_before:
        logger.warning(
            f"Backing up {directory} to {backup_directory} on the "
            f"{remote.hostname} cluster."
        )
        if _exists(backup_directory):
            # _rmtree(backup_directory)
            raise RuntimeError(
                f"The backup directory {backup_directory} to be used as backup for "
                f"{directory} already exists on the {remote.hostname} cluster! "
                f"Refusing to remove it to avoid losing backed up files. "
                f"(Consider logging in to {remote.hostname} and manually restoring "
                f"{directory} from {backup_directory})."
            )
        _copytree(directory, backup_directory)
    else:
        logger.warning(
            f"Test might temporarily create files in {directory} on the "
            f"{remote.hostname} cluster."
        )
    try:
        yield backup_directory
    except Exception:
        logger.critical(
            f"An error occurred while running the test. Will still attempt to restore "
            f"dir {directory} from backup at {backup_directory} on the "
            f"{remote.hostname} cluster!"
        )
        raise
    finally:
        if dir_existed_before:
            logger.warning(
                f"Restoring {directory} from backup at {backup_directory} on the "
                f"{remote.hostname} cluster."
            )
            if _exists(directory):
                _rmtree(directory)
            _copytree(backup_directory, directory)
            _rmtree(backup_directory)
        else:
            logger.warning(
                f"Removing temporarily generated dir {directory} on the "
                f"{remote.hostname} cluster."
            )
            if _exists(directory):
                _rmtree(directory)


@pytest.fixture
def backup_local_ssh_dir():
    """Creates a backup of the ~/.ssh dir on the local machine to `BACKUP_SSH_DIR`."""
    assert (in_github_CI and not in_self_hosted_github_CI) or USE_MY_REAL_SSH_DIR
    ssh_dir = SSH_CONFIG_FILE.parent
    backup_ssh_dir = BACKUP_SSH_DIR
    with backup_dir(ssh_dir, backup_ssh_dir):
        yield backup_ssh_dir


@pytest.fixture
def backup_local_ssh_cache_dir():
    """Creates a backup of the `SSH_CACHE_DIR` dir on the local machine to
    `BACKUP_SSH_CACHE_DIR`."""
    assert (in_github_CI and not in_self_hosted_github_CI) or USE_MY_REAL_SSH_DIR
    ssh_cache_dir = SSH_CACHE_DIR
    backup_ssh_cache_dir = BACKUP_SSH_CACHE_DIR
    with backup_dir(ssh_cache_dir, backup_ssh_cache_dir):
        yield backup_ssh_cache_dir


@pytest.fixture
def backup_remote_ssh_dir(login_node: RemoteV2 | RemoteV1, cluster: str):
    """Creates a backup of the ~/.ssh directory on the remote cluster."""
    if USE_MY_REAL_SSH_DIR:
        logger.critical(
            f"Running a test that is probably going to modify the REAL '~/.ssh' "
            f"directory on the {cluster} cluster! Make sure you know what you're doing!"
            f"(The directory will be backed up to the {BACKUP_REMOTE_SSH_DIR} folder "
            "and should (hopefully) be restored correctly after the tests run (even if "
            "they fail)."
        )
    else:
        assert in_github_CI and not in_self_hosted_github_CI

    home_on_cluster = login_node.get_output("echo $HOME")
    remote_ssh_dir = PurePosixPath(home_on_cluster) / ".ssh"
    backup_remote_ssh_dir = PurePosixPath(home_on_cluster) / BACKUP_REMOTE_SSH_DIR
    with backup_remote_dir(login_node, remote_ssh_dir, backup_remote_ssh_dir):
        yield backup_remote_ssh_dir


@pytest.mark.skipif(
    in_self_hosted_github_CI or not USE_MY_REAL_SSH_DIR,
    reason=(
        "It's a risky to modify the actual SSH directories."
        "Only doing it in the (non-self-hosted) CI runner or if the "
        "USE_MY_REAL_SSH_DIR env var is set."
    ),
)
@pytest.mark.timeout(20)
@pytest.mark.parametrize(
    "passwordless_ssh_was_previously_setup",
    [True, False],
    ids=["already_setup", "not_already_setup"],
)
@pytest.mark.parametrize(
    "user_accepts_registering_key",
    [True, False],
    ids=["accept_registering_key", "reject_registering_key"],
)
def test_setup_passwordless_ssh_access_to_cluster(
    cluster: str,
    login_node: RemoteV1 | RemoteV2,
    input_pipe: PipeInput,
    backup_local_ssh_dir: Path,
    backup_local_ssh_cache_dir: Path,
    backup_remote_ssh_dir: PurePosixPath,
    mocker: pytest_mock.MockerFixture,
    passwordless_ssh_was_previously_setup: bool,
    user_accepts_registering_key: bool,
    file_regression: FileRegressionFixture,
    monkeypatch: pytest.MonkeyPatch,
):
    """Test the function that sets up passwordless SSH to a cluster.

    TODO: This test is way too complex. Need to refactor it later. For now, just
    adapting it to work on the self-hosted CI runner (which should have access to all
    the slurm clusters we care about).

    NOTE: Running this test will make a backup of the ~/.ssh directory in
    `backup_ssh_dir` (~/.ssh_backup), and ~/.cache/ssh to `BACKUP_SSH_CACHE_DIR` and
    restore it after the test.
    For that reason, it currently only runs in the (not self-hosted) GitHub CI runner,
    unless you specifically set the `USE_MY_REAL_SSH_DIR` env variable to '1'.
    """
    assert passwordless_ssh_connection_to_localhost_is_setup
    assert (in_github_CI and not in_self_hosted_github_CI) or USE_MY_REAL_SSH_DIR
    ssh_dir = SSH_CONFIG_FILE.parent

    if cluster == "localhost":
        assert ssh_dir.exists()
        authorized_keys_file = ssh_dir / "authorized_keys"
        backup_authorized_keys_file = backup_local_ssh_dir / "authorized_keys"
        assert backup_authorized_keys_file.exists()
    else:
        authorized_keys_file = PurePosixPath(".ssh/authorized_keys")
        backup_authorized_keys_file = backup_remote_ssh_dir / "authorized_keys"

    ssh_config = paramiko.SSHConfig.from_path(str(SSH_CONFIG_FILE))
    # Get the public and private keys to use for connecting to the cluster.
    ssh_private_key_path = Path(
        ssh_config.lookup(cluster).get("identityfile") or (ssh_dir / "id_rsa")
    )
    ssh_public_key_path = ssh_private_key_path.with_suffix(".pub")
    assert ssh_private_key_path.exists()
    assert ssh_public_key_path.exists()

    def have_passwordless_ssh_access_to(cluster: str) -> bool:
        return is_already_logged_in(cluster, ssh_config_path=SSH_CONFIG_FILE)

    def _exists(file: PurePosixPath | Path):
        if cluster == "localhost":
            assert isinstance(file, Path)
            return file.exists()
        else:
            result = login_node.run(
                f"test -e {file}", display=True, warn=True, hide=True
            )
            if isinstance(result, invoke.runners.Result):
                return result.return_code == 0
            return result.returncode == 0

    def temporarily_disable_ssh_access_to_cluster():
        # NOTE: We already have a backup of the ~/.ssh and ~/.cache/ssh dirs, so we
        # could in principle delete ~/.ssh and it will be restored after the test.
        # todo: Here we take advantage of the fact that we can remove the
        # ~/.ssh/authorized_keys file locally to disable ssh access to localhost.
        # We could do the same on a cluster by temporarily moving
        # ~/.ssh/authorized_keys (on the cluster!) to a backup location.
        if cluster == "localhost":
            # passwordless SSH to localhost should already be setup before tests.
            assert passwordless_ssh_connection_to_localhost_is_setup
            assert isinstance(authorized_keys_file, Path)
            if authorized_keys_file.exists():
                logger.warning(
                    f"Temporarily removing {authorized_keys_file} to disable ssh access to "
                    f"{cluster}. (A backup is available at {backup_authorized_keys_file})"
                )
                authorized_keys_file.unlink()
            # Remove the socket files from currently ongoing ssh connections to the
            # cluster.

            control_path = get_controlpath_for(cluster)
            if control_path.exists():
                # NOTE: There is a hard link of the ssh socket in the backup ssh cache dir.
                # If we close this socket in the ssh cache dir with `ssh -O exit`, the
                # control master process is killed and the socket file is removed, and the
                # "backup" hard link to the same socket file in the backup dir gives
                # "Connection refused" when used with `ssh -O exit` or `ssh -O check`.
                # When used as the controlpath for a regular ssh command (no -O flag), it
                # goes through the login sequence again.
                # Therefore, we only remove the (backed-up) socket file, instead of shutting
                # down the ssh Control master process.

                # The `login_node` uses the control path in the SSH cache dir.
                assert isinstance(login_node, RemoteV2)
                backup_control_path = backup_local_ssh_cache_dir / control_path.name
                assert backup_control_path.exists()
                monkeypatch.setattr(login_node, "control_path", backup_control_path)
                control_path.unlink()
                # subprocess.check_call(
                #     ["ssh", "-O", "exit", f"-oControlPath={control_path}", cluster]
                # )
            assert not is_already_logged_in(cluster)
        else:
            assert passwordless_ssh_was_previously_setup
            if _exists(authorized_keys_file):
                logger.warning(
                    f"Temporarily moving {authorized_keys_file} to {backup_authorized_keys_file} "
                    f"to disable passwordless ssh access to the {cluster} cluster. "
                )
                login_node.run(f"mkdir -p {backup_authorized_keys_file.parent}")
                login_node.run(
                    f"mv {authorized_keys_file} {backup_authorized_keys_file}",
                    warn=False,
                    display=True,
                    hide=False,
                )
            # todo: might not work well for the DRAC clusters!
            assert not is_already_logged_in(cluster)

    def reenable_ssh_access_to_cluster():
        if cluster == "localhost":
            if sys.platform != "win32":
                # Mock running `ssh-copy-id` and re-enable passwordless ssh access by
                # restoring the authorized_keys file.
                # todo: do the equivalent for a remote cluster:
                # remote.run(f"mkdir -p ~/.ssh")
                # remote.run(f"cp {backup_authorized_keys_file} {authorized_keys_file}")
                ssh_dir.mkdir(exist_ok=True, mode=0o700)
                assert not passwordless_ssh_was_previously_setup
                shutil.copy(backup_authorized_keys_file, authorized_keys_file)
                assert isinstance(authorized_keys_file, Path)
                assert authorized_keys_file.stat().st_mode & 0o777 == 0o600
            elif sys.platform == "win32":
                # We're doing the Windows equivalent of ssh-copy-id.
                ssh_dir.mkdir(exist_ok=True, mode=0o700)
                assert not passwordless_ssh_was_previously_setup
                shutil.copy(backup_authorized_keys_file, authorized_keys_file)
            assert is_already_logged_in(cluster)
        else:
            logger.info(
                f"Restoring the original {authorized_keys_file} from backup at "
                f"{backup_authorized_keys_file} on the {cluster} cluster."
            )
            login_node.run(
                f"cp {backup_authorized_keys_file} {authorized_keys_file}",
                warn=False,
                display=True,
                hide=False,
            )

    # NOTE: We already have passwordless ssh access to the cluster, otherwise this test
    # would get skipped (because of the `login_node` fixture.)
    assert have_passwordless_ssh_access_to(cluster)

    if not passwordless_ssh_was_previously_setup:
        # We're not supposed to have the passwordless SSH access to the 'cluster' before
        # running the function under test (which sets it up for us).
        # We're supposed to not already have passwordless SSH access to the 'cluster'.
        assert have_passwordless_ssh_access_to(cluster)
        temporarily_disable_ssh_access_to_cluster()
        assert not have_passwordless_ssh_access_to(cluster)
        # Pass the 'y' input for accepting to register the SSH key on the cluster.
        input_pipe.send_text("y" if user_accepts_registering_key else "n")

    subprocess_run = subprocess.run

    subprocess_run_calls: list[str] = []

    def _mock_subprocess_run(command: tuple[str], *args, **kwargs):
        """Mock of the ssh-copy-id command (otherwise it would wait for a password/2FA).

        Copies the `authorized_keys` file from the backup to the (temporarily cleared)
        .ssh directory.
        """

        if (sys.platform != "win32" and command[0] == "ssh-copy-id") or (
            sys.platform == "win32" and command[-1] == "cat >> ~/.ssh/authorized_keys"
        ):
            # NOTE: `ssh-copy-id` appears to be called all the time, not just when
            # passwordless SSH access is not setup...
            assert not passwordless_ssh_was_previously_setup
            reenable_ssh_access_to_cluster()
            # Mock running `ssh-copy-id` and re-enable passwordless ssh access by
            # restoring the authorized_keys file.
            return subprocess.CompletedProcess(
                command, returncode=0, stdout="", stderr=""
            )
        # Run other commands normally.
        logger.debug(f"Actually running command {command}")
        return subprocess_run(command, *args, **kwargs)

    mock_subprocess_run = mocker.patch("subprocess.run", wraps=_mock_subprocess_run)
    success = setup_passwordless_ssh_access_to_cluster(cluster)
    if passwordless_ssh_was_previously_setup:
        # We already had access to the cluster.
        assert success is True
        assert _exists(authorized_keys_file)
    elif user_accepts_registering_key:
        assert success is True
        assert _exists(authorized_keys_file)
    else:
        # User refuses to register the public key on the cluster.
        assert success is False
        assert not _exists(authorized_keys_file)

    subprocess_run_calls = [
        (
            f"subprocess.run({call.args[0]}"
            + ((", " + ", ".join(other_args)) if (other_args := call.args[1:]) else "")
            + (
                ", " + ", ".join(f"{k}={v}" for k, v in kwargs.items())
                if (kwargs := call.kwargs)
                else ""
            )
            + ")"
        )
        for call in mock_subprocess_run.call_args_list
    ]
    regression_text = "\n".join(
        [
            f"Calling {function_call_string(setup_passwordless_ssh_access_to_cluster, cluster)}",
        ]
        + [
            f"with passwordless SSH access to {cluster} already setup"
            if passwordless_ssh_was_previously_setup
            else "without having setup passwordless SSH access to the cluster beforehand",
        ]
        + [
            f"and the user {'accepting' if user_accepts_registering_key else 'rejecting'} "
            f"to register the new public key on the remote"
        ]
        + [
            "leads to the following commands being executed locally:",
        ]
        + [f"- {call}" for call in subprocess_run_calls]
        + [""],
    )
    regression_text = regression_text.replace(str(SSH_CACHE_DIR), "~/.cache/ssh")
    regression_text = regression_text.replace(str(getpass.getuser()), "<USER>")
    file_regression.check(
        regression_text,
        extension=".md",
    )


@pytest.mark.timeout(10)
@pytest.mark.skipif(
    not ((in_github_CI and not in_self_hosted_github_CI) or USE_MY_REAL_SSH_DIR),
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
    backup_local_ssh_dir: Path,
    input_pipe: PipeInput,
):
    assert in_github_CI or USE_MY_REAL_SSH_DIR
    ssh_dir = Path.home() / ".ssh"
    if ssh_dir.exists():
        logger.warning(
            f"Temporarily deleting the ssh dir (backed up at {backup_local_ssh_dir})"
        )
        shutil.rmtree(ssh_dir)
    ssh_dir.mkdir(mode=0o700, exist_ok=False)

    if not public_key_exists:
        # There should be no ssh keys in the ssh dir before calling the function.
        # We should get a prompt asking if we want to generate a key.
        input_pipe.send_text("y" if accept_generating_key else "n")
    else:
        # There should be an ssh key in the .ssh dir.
        # Won't ask to generate a key.
        create_ssh_keypair(
            ssh_private_key_path=ssh_dir / "id_rsa_milatools", local=LocalV1()
        )
        if drac_clusters_in_ssh_config:
            # We should get a prompt asking if we want to register the public key
            # on the DRAC clusters or not.
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
    import milatools.cli.init

    monkeypatch.setattr(
        milatools.cli.init,
        setup_passwordless_ssh_access_to_cluster.__name__,
        mock_setup_passwordless_ssh_access_to_cluster,
    )

    monkeypatch.setattr(
        milatools.cli.init,
        setup_keys_on_login_node.__name__,
        Mock(spec=setup_keys_on_login_node),
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
