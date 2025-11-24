from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import textwrap
import unittest.mock
from collections.abc import Generator
from functools import partial
from logging import getLogger as get_logger
from pathlib import Path, PosixPath
from typing import Any
from unittest.mock import Mock

import pytest
import pytest_mock
import rich.prompt
from paramiko.config import SSHConfig as SshConfigReader
from pytest_regressions.file_regression import FileRegressionFixture

from milatools.cli import init_command
from milatools.cli.init_command import (
    ON_WINDOWS,
    _get_drac_username,
    _get_mila_username,
    _setup_ssh_config_file,
    copy_ssh_keys_between_wsl_and_windows,
    create_ssh_keypair,
    has_passphrase,
    setup_vscode_settings,
    setup_windows_ssh_config_from_wsl,
)
from milatools.cli.utils import SSHConfig, yn
from tests.conftest import initial_contents

from .common import in_github_cloud_CI, on_windows, xfails_on_windows

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
def input_stream(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Generator[io.StringIO, None, None]:
    """Fixture that creates an input stream to be used to mock user input in tests.

    This creates a `io.StringIO` object that can be read and written to, and is passed
    as the `stream` argument to `rich.prompt.Prompt.ask` and `rich.prompt.Confirm.ask`.
    """
    # with io.StringIO() as s:
    # Note: Using a temporary file would allow us to do relative seeks, which
    # could perhaps be useful for some tests, but for now StringIO works fine and is
    # likely more efficient.
    with open(tmp_path / "fake_input.txt", "w+") as s:
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
        yield s


def test_can_mock_input(input_stream: io.StringIO):
    """Small test just to make sure that our way of passing the input pipe to
    Questionary in tests makes sense.

    TODO: Ideally we'd want to make sure that the input prompts work exactly the same
    way in our tests as they will for the users, but that's not something I'm confident
    I can guarantee.
    """
    input_stream.write("\n".join(["bob", "y", "n"]))
    input_stream.seek(0)
    assert rich.prompt.Prompt.ask("name?") == "bob"
    assert yn("confirm?", default=False) is True
    assert yn("confirm?", default=True) is False


def test_creates_ssh_config_file(ssh_config_file: Path):
    assert ssh_config_file.exists()


@pytest.mark.parametrize(
    ("initial_ssh_config", "entries", "expected_ssh_config"),
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
            # NOTE: This isn't ideal. We'd like the config to be placed before the other
            # existing entry, but this is hard to do.
            """\
            Host beluga narval cedar graham niagara rorqual fir nibi tamia killarney vulcan
              User bob

            Host beluga narval cedar graham niagara
              HostName %h.alliancecan.ca
              User bob
              ControlMaster auto
            """,
            id="config_already_has_more_drac_clusters",
        ),
        pytest.param(
            """\
            Host beluga narval cedar graham niagara
              hostname %h.alliancecan.ca
              User bob
              ControlMaster auto
            """,
            {
                "beluga narval cedar graham niagara rorqual fir nibi tamia killarney vulcan trillium": {
                    "hostname": "%h.alliancecan.ca",
                    "user": "bob",
                    "controlmaster": "auto",
                }
            },
            # Unfortunately, we don't yet have a smart way to update an entry.
            """\
            Host beluga narval cedar graham niagara
              hostname %h.alliancecan.ca
              User bob
              ControlMaster auto

            Host beluga narval cedar graham niagara rorqual fir nibi tamia killarney vulcan trillium
              HostName %h.alliancecan.ca
              User bob
              ControlMaster auto
            """,
            id="config_doesnt_have_new_drac_clusters",
        ),
    ],
)
def test_add_ssh_entry(
    initial_ssh_config: str,
    entries: dict[str, dict[str, Any]],
    expected_ssh_config: str,
    tmp_path: Path,
):
    """Tests that adding an entry to the ssh config file works as expected."""
    config = tmp_path / "config"
    config.write_text(textwrap.dedent(initial_ssh_config))
    ssh_config = SSHConfig(config)

    from milatools.cli.init_command import _add_ssh_entry

    for k, entry in entries.items():
        _add_ssh_entry(
            ssh_config, host=k, entry=entry, _space_before=True, _space_after=True
        )
    resulting_contents = ssh_config.cfg.config()
    assert resulting_contents.strip() == textwrap.dedent(expected_ssh_config).strip()

    # We should expect that if we lookup the entries that we just added (or updated),
    # they should have the values that we just set.
    conf = SshConfigReader.from_text(resulting_contents)
    for host_or_hosts, entry in entries.items():
        for host in host_or_hosts.split():
            conf_entry = conf.lookup(host)
            for option, value in entry.items():
                resolved_value = conf_entry.get(option)
                # conf.lookup(host) will actually resolve of %h to hostname, etc, so we
                # can't directly compare them without also doing that ourselves!
                if "%" in value:
                    continue
                assert resolved_value == value, option


@pytest.mark.xfail(
    in_github_cloud_CI and sys.platform == "darwin",
    reason="Flaky on MacOS GitHub CI, socket.getfqdn takes >10 seconds to run.",
)
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
        "identityfile": unittest.mock.ANY,
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
    input_stream: io.StringIO,
    monkeypatch: pytest.MonkeyPatch,
):
    # TODO: We should probably also have a test that checks that keyboard interrupts
    # work.
    # Seems like the text to send for that would be "\x03".
    ssh_config_path = tmp_path / "config"
    with open(ssh_config_path, "w") as f:
        f.write(contents)
    ssh_config = SSHConfig(ssh_config_path)

    input_stream.write("\n".join(prompt_inputs) + "\n")
    input_stream.seek(0)

    assert _get_mila_username(ssh_config) == expected


@pytest.mark.xfail(
    in_github_cloud_CI and sys.platform == "darwin",
    reason="Flaky on MacOS GitHub CI, socket.getfqdn sometimes takes >10 seconds to run.",
)
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
                  HostName narval.alliancecan.ca
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
                  HostName %h.alliancecan.ca
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
                Host fooo narval bar baz
                    HostName narval.alliancecan.ca
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
    input_stream: io.StringIO,
):
    ssh_config_path = tmp_path / "config"
    with open(ssh_config_path, "w") as f:
        f.write(contents)
    ssh_config = SSHConfig(ssh_config_path)

    input_stream.write("\n".join(prompt_inputs) + "\n")
    input_stream.seek(0)
    assert _get_drac_username(ssh_config) == expected


class TestSetupSshFile:
    @permission_bits_check_doesnt_work_on_windows()
    def test_create_file(self, tmp_path: Path):
        config_path = tmp_path / "config"
        file = _setup_ssh_config_file(config_path)
        assert file.exists()
        assert file.stat().st_mode & 0o777 == 0o600

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
def mila_username(request: pytest.FixtureRequest) -> str:
    return getattr(request, "param", "bob")


@pytest.fixture
def drac_username(request: pytest.FixtureRequest) -> str:
    return getattr(request, "param", "bob")


@pytest.fixture
def linux_ssh_config(
    ssh_config_file: Path,
) -> SSHConfig:
    """Creates the SSH config that is generated by `mila init` on a Linux machine."""
    if sys.platform == "win32":
        pytest.skip("Only relevant on Linux or MacOS.")
    return SSHConfig(ssh_config_file)


@pytest.mark.parametrize("accept_changes", [True, False], ids=["accept", "reject"])
def test_setup_windows_ssh_config_from_wsl(
    pretend_to_be_in_WSL,  # here even if `windows_home` already uses it (more explicit)
    windows_home: Path,
    linux_ssh_config: SSHConfig,
    file_regression: FileRegressionFixture,
    fake_linux_ssh_keypair: tuple[Path, Path],  # add this fixture so the keys exist.
    accept_changes: bool,
    input_stream: io.StringIO,
    linux_home: Path,
):
    # The references to the linux SSH keys in "home" need to be adjusted to point to the
    # home during testing.
    # This way, we can check that running the function will correctly change the
    # identityfile from linux to windows home.
    linux_ssh_config.path.write_text(
        linux_ssh_config.path.read_text()
        .replace("~", str(linux_home))
        # Real home directory:
        .replace(os.environ["HOME"], str(linux_home))
    )
    linux_ssh_config = SSHConfig(linux_ssh_config.path)

    initial_contents = linux_ssh_config.cfg.config()
    linux_ssh_dir = linux_ssh_config.path.parent
    assert isinstance(linux_ssh_dir, PosixPath)
    windows_ssh_config_path = windows_home / ".ssh" / "config"

    initial_pos = input_stream.seek(0, 1)  # get initial position in the buffer
    input_stream.write("y\n" if accept_changes else "n\n")
    input_stream.seek(initial_pos)  # reset cursor to initial position.

    if not accept_changes:
        with contextlib.suppress(SystemExit):
            setup_windows_ssh_config_from_wsl(
                ssh_dir=linux_ssh_dir, linux_ssh_config=linux_ssh_config
            )
    else:
        setup_windows_ssh_config_from_wsl(
            ssh_dir=linux_ssh_dir, linux_ssh_config=linux_ssh_config
        )
    if accept_changes:
        identity_file_entries = (
            SSHConfig(windows_ssh_config_path).lookup("mila").get("identityfile")
        )
        # Unclear why paramiko returns a list here.
        assert isinstance(identity_file_entries, list)
        assert len(identity_file_entries) == 1
        assert Path(identity_file_entries[0]) == (windows_home / ".ssh" / "id_rsa_mila")

    assert windows_ssh_config_path.exists()
    assert windows_ssh_config_path.stat().st_mode & 0o777 == 0o600
    assert windows_ssh_config_path.parent.stat().st_mode & 0o777 == 0o700
    if not accept_changes:
        assert windows_ssh_config_path.read_text() == ""

    initial_contents = initial_contents.replace(
        str(windows_home), "<WINDOWS_HOME>"
    ).replace(str(linux_home), "<WSL_HOME>")
    actual_contents = (
        windows_ssh_config_path.read_text()
        .replace(str(windows_home), "<WINDOWS_HOME>")
        .replace(str(linux_home), "<WSL_HOME>")
    )
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
            actual_contents,
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
    windows_home: Path,
    linux_home: PosixPath,
    fake_linux_ssh_keypair: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    input_stream: io.StringIO,
):
    linux_public_key_path, linux_private_key_path = fake_linux_ssh_keypair
    prompt_inputs = [
        "y",  # accept creating the Windows config file
        "y",  # accept the changes
    ]
    initial_position = input_stream.seek(0, 1)
    input_stream.write("\n".join(prompt_inputs) + "\n")
    input_stream.seek(initial_position)

    setup_windows_ssh_config_from_wsl(
        linux_home / ".ssh", linux_ssh_config=linux_ssh_config
    )

    windows_private_key_path = windows_home / linux_private_key_path.relative_to(
        linux_home
    )
    windows_public_key_path = windows_private_key_path.with_suffix(".pub")

    # TODO: Check that the copied key has the correct permissions (and content) on **WINDOWS**.
    assert windows_private_key_path.exists()
    assert windows_private_key_path.stat().st_mode & 0o777 == 0o600
    assert windows_public_key_path.exists()
    assert windows_public_key_path.stat().st_mode & 0o777 == 0o644
    # todo: Might have to manually add the weird CRLF line endings to the public/private
    # key file?
    assert windows_private_key_path.read_text() == linux_private_key_path.read_text()
    assert windows_public_key_path.read_text() == linux_public_key_path.read_text()


@pytest.fixture(scope="function")
def linux_ssh_dir(linux_home: PosixPath) -> PosixPath:
    ssh_dir = linux_home / ".ssh"
    ssh_dir.mkdir(mode=0o700, exist_ok=True, parents=True)
    return ssh_dir


@pytest.fixture(scope="function")
def linux_ssh_key(linux_ssh_dir: Path):
    # This is a ssh key in a mock ssh directory in a temporary folder.
    # It can't be used to connect to anything.
    private_key_path = linux_ssh_dir / "id_rsa_test"
    create_ssh_keypair(private_key_path, passphrase="")
    return private_key_path


# On Windows, we can't create files in the WSL directory with the right permissions.
# This works when run from WSL, but not when run from Windows directly.
# Perhaps we could invoke WSL from Windows to do this copy and chmod?
# if shutil.which("wsl.exe") is not None: do something


@pytest.mark.skipif(ON_WINDOWS, reason="Can't run on Windows.")
def test_copy_keys_from_wsl_to_windows(
    windows_home: Path,
    linux_ssh_dir: PosixPath,
    linux_home: PosixPath,
    linux_ssh_key: Path,
):
    # This test should be run from WSL.
    linux_private_key_path = linux_ssh_key
    linux_public_key_path = linux_ssh_key.with_suffix(".pub")
    copy_ssh_keys_between_wsl_and_windows(linux_ssh_dir)

    windows_ssh_dir = windows_home / ".ssh"
    windows_public_key_path = windows_ssh_dir / linux_public_key_path.name
    windows_private_key_path = windows_ssh_dir / linux_private_key_path.name

    assert windows_private_key_path.exists()
    assert windows_private_key_path.stat().st_mode & 0o777 == 0o600
    assert windows_private_key_path.read_text() == linux_private_key_path.read_text()
    # Has Windows line endings.
    assert windows_private_key_path.read_bytes().count(b"\r\n") != 0

    assert windows_public_key_path.exists()
    assert windows_public_key_path.stat().st_mode & 0o777 == 0o644
    assert windows_public_key_path.read_text() == linux_public_key_path.read_text()
    assert windows_public_key_path.read_bytes().count(b"\r\n") != 0


@pytest.mark.skipif(ON_WINDOWS, reason="Can't run on Windows.")
def test_copy_keys_from_windows_to_wsl(
    windows_home: Path,
    linux_home: PosixPath,
    fake_linux_ssh_keypair: tuple[Path, Path],
):
    # This test should be run from WSL.
    linux_public_key_path, linux_private_key_path = fake_linux_ssh_keypair
    windows_ssh_dir = windows_home / ".ssh"
    assert not windows_ssh_dir.exists()

    copy_ssh_keys_between_wsl_and_windows(linux_home / ".ssh")
    # Previous test shows that these are created correctly.
    windows_public_key_path = windows_ssh_dir / linux_public_key_path.name
    windows_private_key_path = windows_ssh_dir / linux_private_key_path.name

    # Delete the WSL ssh directory to test copying back from Windows to WSL.
    shutil.rmtree(linux_home / ".ssh")

    copy_ssh_keys_between_wsl_and_windows(linux_home / ".ssh")

    wsl_ssh_dir = Path.home() / ".ssh"
    wsl_public_key_path = wsl_ssh_dir / linux_public_key_path.name
    wsl_private_key_path = wsl_ssh_dir / linux_private_key_path.name

    assert wsl_private_key_path.exists()
    assert wsl_private_key_path.stat().st_mode & 0o777 == 0o600
    assert wsl_private_key_path.read_text() == windows_private_key_path.read_text()
    assert wsl_private_key_path.read_bytes().count(b"\r\n") == 0

    assert wsl_public_key_path.exists()
    assert wsl_public_key_path.stat().st_mode & 0o777 == 0o644
    assert wsl_public_key_path.read_text() == windows_public_key_path.read_text()
    assert wsl_public_key_path.read_bytes().count(b"\r\n") == 0
