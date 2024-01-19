from __future__ import annotations

import filecmp
import json
import shutil
import tarfile
from pathlib import Path
from typing import Any

import pytest
from typing_extensions import Literal, get_args

from milatools.cli.remote import Remote
from milatools.cli.vscode_utils import (
    EXTENSIONS_ARCHIVE_NAME,
    copy_vscode_extensions_to_remote,
    get_expected_vscode_settings_json_path,
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


def assert_files_were_copied_correctly(source: Path, dest: Path):
    """Checks that all files in `source` have been copied to `dest`."""

    dirs_comparison = filecmp.dircmp(source, dest)
    dirs_comparison.report_full_closure()
    # note: we allow files to only be present in `dest` but not in source.
    assert (
        not dirs_comparison.left_only
    ), f"Files in source but not in dest: {dirs_comparison.left_only}"
    assert (
        not dirs_comparison.diff_files
    ), f"Files with different contents: {dirs_comparison.diff_files}"


def test_assert_files_were_copied_correctly(tmp_path: Path):
    source = tmp_path / "src"
    source.mkdir()

    dest = tmp_path / "dest"
    dest.mkdir()

    (source / "file1.txt").write_text("foo")

    with pytest.raises(AssertionError, match="file1.txt"):
        assert_files_were_copied_correctly(source, dest)

    (dest / "file1.txt").write_text("foobar")

    with pytest.raises(AssertionError, match="file1.txt"):
        assert_files_were_copied_correctly(source, dest)

    (dest / "file1.txt").write_text("foo")
    assert_files_were_copied_correctly(source, dest)


def test_copy_vscode_extensions_to_clean_remote(
    remote: Remote,
    tmp_path_factory: pytest.TempPathFactory,
    fake_vscode_extensions_dirs: tuple[Path, Path],
):
    """Test the case where the remote has no vscode extensions installed yet."""
    if remote.hostname != "localhost" or remote.connection.host != "localhost":
        pytest.skip("This test only works when the connection is to localhost.")

    # Setup a fake vscode folder with some fake extensions, following the same kind of
    # structure as a real vscode extensions folder.
    local_extensions_dir, fake_remote_extensions_dir = fake_vscode_extensions_dirs

    fake_local_milatools_dir = tmp_path_factory.mktemp("fake_local_milatools")
    fake_remote_milatools_dir = tmp_path_factory.mktemp("fake_remote_milatools")

    copy_vscode_extensions_to_remote(
        cluster="localhost",  # type: ignore
        local_vscode_extensions_dir=local_extensions_dir,
        remote=remote,
        local_milatools_dir=fake_local_milatools_dir,
        # NOTE: This arg is taken as a string just to signify that it's a path on the
        # remote, not a local path. In this case here, we're using a local path and
        # pretending that it's on the remote (since the host is localhost)
        remote_vscode_extensions_dir=str(fake_remote_extensions_dir),
        remote_milatools_dir=str(fake_remote_milatools_dir),
    )

    # Check that an archive was created and that it contains the expected files.
    temp_folder = tmp_path_factory.mktemp("temp")

    # Extract the archive to a temp folder and check that the contents match the local
    # extensions folder
    local_extensions_archive_path = fake_local_milatools_dir / EXTENSIONS_ARCHIVE_NAME
    assert local_extensions_archive_path.exists()
    with tarfile.open(local_extensions_archive_path, mode="r:gz") as tar:
        tar.extractall(temp_folder)
    assert_files_were_copied_correctly(local_extensions_dir, temp_folder)

    # Check that the archive was copied to the remote.
    assert_files_were_copied_correctly(local_extensions_dir, fake_remote_extensions_dir)


def test_pack_extensions_into_archive(tmp_path_factory: pytest.TempPathFactory):
    extensions_dir = tmp_path_factory.mktemp("extensions")
    extensions: list[tuple[str, str]] = [
        ("ms-python-python", "2023.22.1"),
        ("other_extension", "2023.22.1"),
    ]
    missing_extensions = extensions[:-1]

    make_fake_vscode_extensions_folder(
        extensions_dir=extensions_dir,
        extensions=extensions,
    )
    import shutil

    archive_path = tmp_path_factory.mktemp(".milatools") / "extensions.tar.gz"

    pack_vscode_extensions_into_archive(
        archive_path,
        extensions=[
            f"{ext_id}-{ext_version}" for ext_id, ext_version in missing_extensions
        ],
        local_vscode_extensions_dir=extensions_dir,
    )

    tempdir = tmp_path_factory.mktemp("temp")
    assert archive_path.exists()
    shutil.unpack_archive(
        archive_path,
        extract_dir=tempdir,
    )

    # TODO: Do we need to consolidate the local and remote extensions.json files? So far
    # it doesn't seem like it, seems like VsCode just figures out which extensions to
    # use. A bit unsure about this though, it would be good to test this further.

    # assert_files_were_copied_correctly(
    #     extensions_dir / "extensions.json", tempdir / "extensions.json"
    # )
    for extension_id, version in extensions:
        extension_rel_path = f"{extension_id}-{version}"
        if (extension_id, version) in missing_extensions:
            assert_files_were_copied_correctly(
                extensions_dir / extension_rel_path, tempdir / extension_rel_path
            )
        else:
            assert not (tempdir / extension_rel_path).exists()
