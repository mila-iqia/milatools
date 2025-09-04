from __future__ import annotations

import contextlib
import errno
import io
import json
import os
import shutil
import subprocess
import sys
import textwrap
from functools import partial
from logging import getLogger as get_logger
from pathlib import Path, PurePosixPath
from typing import Any
from unittest.mock import Mock

import invoke
import pytest
import pytest_mock
import questionary
import questionary.prompts
import questionary.prompts.confirm
import rich.prompt
from paramiko.config import SSHConfig as SshConfigReader
from prompt_toolkit.input import PipeInput, create_pipe_input
from pytest_regressions.file_regression import FileRegressionFixture

from milatools.cli import init_command
from milatools.cli.init_command import (
    _get_drac_username,
    _get_mila_username,
    _setup_ssh_config_file,
    create_ssh_keypair,
    has_passphrase,
    setup_ssh_config,
    setup_vscode_settings,
    setup_windows_ssh_config_from_wsl,
)
from milatools.cli.utils import SSHConfig
from milatools.utils.remote_v1 import RemoteV1
from milatools.utils.remote_v2 import SSH_CACHE_DIR, SSH_CONFIG_FILE, RemoteV2
from tests.conftest import initial_contents

from .common import (
    in_github_CI,
    in_self_hosted_github_CI,
    on_windows,
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


# Set a module-level mark: Each test cannot take longer than X second to run.
pytestmark = pytest.mark.timeout(10)


@pytest.fixture
def input_pipe(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest):
    """Fixture that creates an input pipe and makes questionary use it.

    TODO: This is super uber ugly to use. We should switch to something like a
    known_question to answer mapping instead.

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


def test_creates_ssh_config_file(ssh_config_file: Path):
    assert ssh_config_file.exists()


@pytest.mark.parametrize(
    ("initial_contents", "entries", "expected_contents"),
    [
        pytest.param(
            "",
            {"mila": {"hostname": "login.server.mila.quebec", "user": "bob"}},
            """\
            Host mila
              HostName login.server.mila.quebec
              User bob
            """,
            id="empty_file_add_mila",
        ),
        pytest.param(
            """\
            Host mila
              HostName login.server.mila.quebec
              User bob
            """,
            {"mila": {"hostname": "login.server.mila.quebec", "user": "bob"}},
            """\
            Host mila
              HostName login.server.mila.quebec
              User bob
            """,
            id="add_exactly_same_entry",
        ),
        pytest.param(
            """\
            Host mila
              HostName login.server.mila.quebec
              User bob
            """,
            {
                "mila": {
                    "hostname": "login.server.mila.quebec",
                    "user": "bob",
                    "controlmaster": "auto",
                }
            },
            """\
            Host mila
              HostName login.server.mila.quebec
              User bob
              ControlMaster auto
            """,
            id="add_key_to_entry",
        ),
        pytest.param(
            """\
            Host mila
              HostName login.server.mila.quebec
              User bob
            """,
            {"mila": {"controlmaster": "auto"}},
            """\
            Host mila
              HostName login.server.mila.quebec
              User bob
              ControlMaster auto
            """,
            id="add_key_to_entry_2",
        ),
        pytest.param(
            "",
            {
                "beluga narval cedar graham niagara": {
                    "hostname": "%h.alliancecan.ca",
                    "user": "bob",
                    "controlmaster": "auto",
                }
            },
            """\
            Host beluga narval cedar graham niagara
              HostName %h.alliancecan.ca
              User bob
              ControlMaster auto
            """,
            id="empty_add_drac",
        ),
        pytest.param(
            "",
            {
                "mila": {
                    "hostname": "login.server.mila.quebec",
                    "user": "bob",
                },
                "beluga narval cedar graham niagara": {
                    "hostname": "%h.alliancecan.ca",
                    "user": "bob",
                    "controlmaster": "auto",
                },
            },
            """\
            Host mila
              HostName login.server.mila.quebec
              User bob

            Host beluga narval cedar graham niagara
              HostName %h.alliancecan.ca
              User bob
              ControlMaster auto
            """,
            id="empty_add_drac_and_mila",
        ),
        pytest.param(
            """\
            Host beluga narval cedar graham niagara rorqual fir nibi tamia killarney vulcan
              User bob
            """,
            {
                "beluga narval cedar graham niagara": {
                    "hostname": "%h.alliancecan.ca",
                    "user": "bob",
                    "controlmaster": "auto",
                }
            },
            """\
            Host beluga narval cedar graham niagara rorqual fir nibi tamia killarney vulcan
              ControlMaster auto
              User bob
              HostName %h.alliancecan.ca
            """,
            id="config_already_has_more_drac_clusters",
        ),
    ],
)
def test_add_ssh_entry(
    initial_contents: str,
    entries: dict[str, dict[str, Any]],
    expected_contents: str,
    tmp_path: Path,
):
    """Tests that adding an entry to the ssh config file works as expected."""
    config = tmp_path / "config"
    config.write_text(textwrap.dedent(initial_contents))
    ssh_config = SSHConfig(config)

    from milatools.cli.init_command import _add_ssh_entry

    for k, entry in entries.items():
        _add_ssh_entry(
            ssh_config, host=k, entry=entry, _space_before=True, _space_after=True
        )
    resulting_contents = ssh_config.cfg.config()
    assert resulting_contents.strip() == textwrap.dedent(expected_contents).strip()


@pytest.mark.parametrize(
    initial_contents.__name__,
    [
        """\
        Host *.server.mila.quebec
            User bob
        """
    ],
    indirect=True,
)
def test_fixes_overly_general_cn_entry(
    ssh_config_file: Path, mila_username: str | None
):
    """Test the case where the user has a *.server.mila.quebec entry."""
    assert mila_username
    ssh_config = SshConfigReader.from_path(ssh_config_file)
    assert ssh_config.lookup("cn-a001.server.mila.quebec") == {
        "hostname": "cn-a001.server.mila.quebec",
        "user": mila_username,
        "proxyjump": "mila",
    }
    assert "proxyjump" not in ssh_config.lookup("login.server.mila.quebec")
    assert "proxyjump" not in ssh_config.lookup("login-1.login.server.mila.quebec")


@pytest.mark.parametrize(
    ("contents", "prompt_inputs", "expected"),
    [
        pytest.param(
            "",  # empty file.
            ["y", "bob"],  # Yes I have a mila account, then write "bob" then enter.
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
            [
                "y",
                "bob",
            ],  # Yes I have a mila account (still asks), and username is bob.
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
            [],
            "george",  # first entry wins based on the rules of SSH config.
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
            ["N"],  # Don't have a Mila account, dont ask me for the username.
            None,
            id="empty_username",
        ),
    ],
)
def test_get_username(
    contents: str,
    prompt_inputs: list[str],
    expected: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    # TODO: We should probably also have a test that checks that keyboard interrupts
    # work.
    # Seems like the text to send for that would be "\x03".
    ssh_config_path = tmp_path / "config"
    with open(ssh_config_path, "w") as f:
        f.write(contents)
    ssh_config = SSHConfig(ssh_config_path)

    import rich.prompt

    with io.StringIO() as s:
        s.write("\n".join(prompt_inputs) + "\n")
        s.seek(0)
        monkeypatch.setattr(
            rich.prompt.Confirm,
            rich.prompt.Confirm.ask.__name__,
            Mock(
                spec=rich.prompt.Confirm.ask,
                side_effect=partial(rich.prompt.Confirm.ask, stream=s),
            ),
        )
        monkeypatch.setattr(
            rich.prompt.Prompt,
            rich.prompt.Prompt.ask.__name__,
            Mock(
                spec=rich.prompt.Prompt.ask,
                side_effect=partial(rich.prompt.Prompt.ask, stream=s),
            ),
        )

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
            "george",  # first entry wins, based on the rules of SSH config.
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
            # Yes, then an invalid username (space), then a
            # real username.
            ["y\r", " \r", "bob\r"],
            "bob",
            id="empty_username",
        ),
    ],
)
def test_get_drac_username(
    contents: str,
    prompt_inputs: list[str],
    expected: str | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    ssh_config_path = tmp_path / "config"
    with open(ssh_config_path, "w") as f:
        f.write(contents)
    ssh_config = SSHConfig(ssh_config_path)

    with io.StringIO() as s:
        s.write("\n".join(prompt_inputs) + "\n")
        s.seek(0)
        monkeypatch.setattr(
            rich.prompt.Confirm,
            rich.prompt.Confirm.ask.__name__,
            Mock(
                spec=rich.prompt.Confirm.ask,
                side_effect=partial(rich.prompt.Confirm.ask, stream=s),
            ),
        )
        monkeypatch.setattr(
            rich.prompt.Prompt,
            rich.prompt.Prompt.ask.__name__,
            Mock(
                spec=rich.prompt.Prompt.ask,
                side_effect=partial(rich.prompt.Prompt.ask, stream=s),
            ),
        )
        # mocker.patch("rich.prompt.Prompt.ask", spec_set=True, side_effects=prompt_inputs)

        assert _get_drac_username(ssh_config) == expected


class TestSetupSshFile:
    @permission_bits_check_doesnt_work_on_windows()
    def test_create_file(self, tmp_path: Path):
        config_path = tmp_path / "config"
        file = _setup_ssh_config_file(config_path)
        assert file.exists()
        assert file.stat().st_mode & 0o777 == 0o600

    @pytest.mark.xfail(
        strict=True,
        reason="Does not ask whether or not to create the SSH config anymore, just does it.",
    )
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

    @permission_bits_check_doesnt_work_on_windows()
    def test_creates_dir(self, tmp_path: Path):
        config_path = tmp_path / "fake_ssh" / "config"
        file = _setup_ssh_config_file(config_path)
        assert file.parent.exists()
        assert file.parent.stat().st_mode & 0o777 == 0o700
        assert file.exists()
        assert file.stat().st_mode & 0o777 == 0o600

    @permission_bits_check_doesnt_work_on_windows()
    @pytest.mark.parametrize(
        "file_exists",
        [
            True,
            False,
        ],
    )
    def test_fixes_dir_permission_issues(self, file_exists: bool, tmp_path: Path):
        config_path = tmp_path / "fake_ssh" / "config"
        config_path.parent.mkdir(mode=0o777)  # some bad permission for the SSH dir.
        if file_exists:
            config_path.touch(mode=0o777)  # some bad permission for the SSH file.
        # Config file doesn't exist yet.
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
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mocker: pytest_mock.MockerFixture,
) -> SSHConfig:
    """Creates the SSH config that is generated by `mila init` on a Linux machine."""
    # Enter username, accept fixing that entry, then confirm.
    ssh_config_path = tmp_path / "ssh_config"

    # for prompt in [
    #     "bob\r",  # What's your username on the Mila cluster?
    #     "y",  # Do you also have a DRAC account?
    #     "bob\r",  # username on DRAC
    #     "y",  # accept adding the entries in the ssh config
    # ]:
    #     input_pipe.send_text(prompt)

    mocker.patch("rich.prompt.Prompt.ask", spec_set=True, side_effect=["bob", "bob"])
    mocker.patch(
        "rich.prompt.Confirm.ask",
        spec_set=True,
        return_value=True,
    )

    if sys.platform.startswith("win"):
        pytest.skip(
            "TODO: Issue when changing sys.platform to get the Linux config when "
            "on Windows."
        )
    setup_ssh_config(ssh_config_path)

    return SSHConfig(ssh_config_path)


@pytest.mark.parametrize("accept_changes", [True, False], ids=["accept", "reject"])
def test_setup_windows_ssh_config_from_wsl(
    pretend_to_be_in_WSL,  # here even if `windows_home` already uses it (more explicit)
    windows_home: Path,
    linux_ssh_config: SSHConfig,
    file_regression: FileRegressionFixture,
    fake_linux_ssh_keypair: tuple[Path, Path],  # add this fixture so the keys exist.
    accept_changes: bool,
    mocker: pytest_mock.MockerFixture,
):
    initial_contents = linux_ssh_config.cfg.config()
    windows_ssh_config_path = windows_home / ".ssh" / "config"

    mocker.patch("rich.prompt.Confirm.ask", spec_set=True, return_value=accept_changes)
    if not accept_changes:
        with contextlib.suppress(SystemExit):
            setup_windows_ssh_config_from_wsl(linux_ssh_config=linux_ssh_config)
    else:
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
            f"and this user input: {'y' if accept_changes else 'n'}",
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
    initial_settings: dict | None,
    file_regression: FileRegressionFixture,
    accept_changes: bool,
    mocker: pytest_mock.MockerFixture,
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
    mocker.patch("rich.prompt.Confirm.ask", spec_set=True, return_value=accept_changes)

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


@pytest.fixture
def linux_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Creates a fake home directory where we will make a fake SSH directory for
    tests."""
    linux_home = tmp_path / "fake_linux_home"
    linux_home.mkdir(exist_ok=False)

    monkeypatch.setattr(Path, "home", Mock(spec=Path.home, return_value=linux_home))
    return linux_home


@pytest.fixture
def fake_linux_ssh_keypair(linux_home: Path):
    """Creates a fake ssh key pair in some mock ssh directory.

    Used in tests related to mila init and WSL.
    """

    fake_linux_ssh_dir = linux_home / ".ssh"
    fake_linux_ssh_dir.mkdir(mode=0o700)

    private_key_text = "THIS IS A PRIVATE KEY"
    linux_private_key_path = fake_linux_ssh_dir / "id_rsa"
    linux_private_key_path.write_text(private_key_text)
    linux_private_key_path.chmod(mode=0o600)

    public_key_text = "THIS IS A PUBLIC KEY"
    linux_public_key_path = linux_private_key_path.with_suffix(".pub")
    linux_public_key_path.write_text(public_key_text)
    linux_public_key_path.chmod(mode=0o600)

    return linux_public_key_path, linux_private_key_path


def test_setup_windows_ssh_config_from_wsl_copies_keys(
    linux_ssh_config: SSHConfig,
    input_pipe: PipeInput,
    windows_home: Path,
    linux_home: Path,
    fake_linux_ssh_keypair: tuple[Path, Path],
):
    linux_public_key_path, linux_private_key_path = fake_linux_ssh_keypair

    input_pipe.send_text("y")  # accept creating the Windows config file
    input_pipe.send_text("y")  # accept the changes

    setup_windows_ssh_config_from_wsl(linux_ssh_config=linux_ssh_config)

    windows_private_key_path = windows_home / linux_private_key_path.relative_to(
        linux_home
    )
    windows_public_key_path = windows_private_key_path.with_suffix(".pub")

    # TODO: Check that the copied key has the correct permissions (and content) on **WINDOWS**.
    assert windows_private_key_path.exists()
    assert windows_private_key_path.stat().st_mode & 0o777 == 0o600
    assert windows_public_key_path.exists()
    assert windows_public_key_path.stat().st_mode & 0o777 == 0o600
    # todo: Might have to manually add the weird CRLF line endings to the public/private
    # key file?
    assert windows_private_key_path.read_text() == linux_private_key_path.read_text()
    assert windows_public_key_path.read_text() == linux_public_key_path.read_text()


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
