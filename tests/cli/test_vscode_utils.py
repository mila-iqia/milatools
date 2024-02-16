from __future__ import annotations

import filecmp
import json
import shutil
import tarfile
from pathlib import Path
from typing import Any, Literal, get_args

import pytest

from milatools.cli.remote import Remote
from milatools.cli.vscode_utils import (
    EXTENSIONS_ARCHIVE_NAME,
    get_expected_vscode_settings_json_path,
    install_vscode_extensions_on_remote,
    pack_vscode_extensions_into_archive,
    vscode_installed,
)


def test_vscode_installed(monkeypatch: pytest.MonkeyPatch):
    """Check that on a dev machine with VsCode installed, this function returns True."""
    monkeypatch.setenv("PATH", "")
    assert not vscode_installed()


@pytest.mark.parametrize("custom_code_command", ["ls", "fake-code-doesnt-exist"])
def test_vscode_installed_with_env_var(
    custom_code_command: str, monkeypatch: pytest.MonkeyPatch
):
    """Check the fn returns whether the custom vscode command is available when set."""
    monkeypatch.setenv("MILATOOLS_CODE_COMMAND", custom_code_command)
    assert vscode_installed() == bool(shutil.which(custom_code_command))


@pytest.mark.xfail(
    not vscode_installed(),
    reason="This machine doesn't have VsCode installed.",
    strict=True,
)
def test_get_expected_vscode_settings_json_path():
    """Check that on a dev machine with VsCode the expected path points to a real
    file."""
    expected_path = get_expected_vscode_settings_json_path()
    print(expected_path)
    assert expected_path.exists()
    # NOTE: Seems like there can sometimes be some issues reading some of these setting
    # files. Leaving this commented for now.
    # with open(expected_path, "r") as f:
    #     settings_dict = json.load(f)
    # assert isinstance(settings_dict, dict)


RemoteVsCodeExtensionsState = Literal[
    "clean", "some_extensions", "all_extensions", "extra_extensions"
]


@pytest.fixture(params=get_args(RemoteVsCodeExtensionsState))
def remote_vscode_extensions_state(
    request: pytest.FixtureRequest,
) -> RemoteVsCodeExtensionsState:
    """Used to parametrize the initial state of vscode extensions on the remote."""
    return request.param


@pytest.fixture
def fake_vscode_extensions_dirs(
    tmp_path_factory: pytest.TempPathFactory,
    remote_vscode_extensions_state: RemoteVsCodeExtensionsState,
) -> tuple[Path, Path]:
    """Fixture that sets up a fake vscode extensions folder for the test(s) below."""

    local_vscode_extensions_dir = tmp_path_factory.mktemp("local_extensions")

    # An example entry from a real extensions.json file.
    # This is taken from my file at ~/.vscode/extensions/extensions.json.

    local_extensions: list[tuple[str, str]] = [
        ("ms-python-python", "2023.22.1"),
        ("some-fake-extension", "2024.1.1"),
        ("some-fake-extension", "2024.1.2"),
        ("other-fake-extension", "2022.1.1"),
    ]

    make_fake_vscode_extensions_folder(
        extensions_dir=local_vscode_extensions_dir, extensions=local_extensions
    )

    fake_remote_vscode_extensions_dir = tmp_path_factory.mktemp("remote_extensions")

    if remote_vscode_extensions_state == "clean":
        # No extensions on the remote.
        # Don't create the remote vscode extensions directory (so here we actually need
        # to remove the dir).
        fake_remote_vscode_extensions_dir.rmdir()

    elif remote_vscode_extensions_state == "some_extensions":
        # Some extensions are to be already present in the remote vscode extensions dir.
        remote_extensions = local_extensions[:-1]
        make_fake_vscode_extensions_folder(
            extensions_dir=fake_remote_vscode_extensions_dir,
            extensions=remote_extensions,
        )

    elif remote_vscode_extensions_state == "all_extensions":
        # All extensions are to be already present the remote.
        # note: removing the dir temporarily because we can't pass dirs_exist_ok below
        # in python 3.7
        fake_remote_vscode_extensions_dir.rmdir()
        shutil.copytree(
            local_vscode_extensions_dir,
            fake_remote_vscode_extensions_dir,
            # dirs_exist_ok=True,
        )

    else:
        assert remote_vscode_extensions_state == "extra_extensions"
        # There are some extra extensions on the remote that are not on the local.
        remote_extensions = local_extensions + [("extra-fake-extension", "2022.1.1")]
        make_fake_vscode_extensions_folder(
            extensions_dir=fake_remote_vscode_extensions_dir,
            extensions=remote_extensions,
        )

    return local_vscode_extensions_dir, fake_remote_vscode_extensions_dir


def make_fake_vscode_extensions_folder(
    extensions_dir: Path, extensions: list[tuple[str, str]]
) -> None:
    if not extensions:
        # No extensions = a clean remote with no extensions.
        return
    (extensions_dir / "extensions.json").write_text(
        json.dumps(
            [
                _fake_vscode_extension_json_entry(extension_id, version)
                for extension_id, version in extensions
            ]
        )
    )
    for extension_id, version in extensions:
        _make_fake_vscode_extension(
            vscode_extensions_dir=extensions_dir,
            extension_id=extension_id,
            version=version,
        )


def _fake_vscode_extension_json_entry(
    extension_id: str = "ms-python-python", version: str = "2023.22.1"
) -> dict[str, Any]:
    return {
        "identifier": {
            "id": extension_id,
            "uuid": "f1f59ae4-9318-4f3c-a9b5-81b2eaa5f8a5",
        },
        "version": "2023.22.1",
        "location": {
            "$mid": 1,
            # TODO: Check if we need to update this path in each entry once we copy
            # the extension folders to the remote, or if this gets automatically set
            # to the right path using the `relativeLocation` field.
            "path": f"/home/fabrice/.vscode/extensions/{extension_id}-{version}",
            "scheme": "file",
        },
        "relativeLocation": f"{extension_id}-{version}",
        "metadata": {
            "id": "f1f59ae4-9318-4f3c-a9b5-81b2eaa5f8a5",
            "publisherId": "998b010b-e2af-44a5-a6cd-0b5fd3b9b6f8",
            "publisherDisplayName": "Microsoft",
            "targetPlatform": "undefined",
            "isApplicationScoped": False,
            "updated": True,
            "isPreReleaseVersion": False,
            "installedTimestamp": 1702999774872,
            "preRelease": False,
        },
    }


def _make_fake_vscode_extension(
    vscode_extensions_dir: Path,
    extension_id: str = "ms-python-python",
    version: str = "2023.22.1",
) -> Path:
    extension_dir = vscode_extensions_dir / f"{extension_id}-{version}"
    extension_dir.mkdir()

    (extension_dir / "package.json").write_text(
        json.dumps(
            {
                "name": "python",
                "displayName": "Python",
                "description": "...",
                "version": f"{version}",
                "foo": "bar",
            }
        )
    )
    # Add some dummy files to the extension directory.
    (extension_dir / "pythonExtensionApi" / "src").mkdir(parents=True)
    (extension_dir / "pythonExtensionApi" / "src" / "bob.txt").write_text("foo")
    return extension_dir
