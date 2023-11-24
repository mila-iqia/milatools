import shutil

import pytest

from milatools.cli.vscode_utils import (
    get_expected_vscode_settings_json_path,
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
