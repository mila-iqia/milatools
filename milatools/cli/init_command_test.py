"""Tests for the `mila init` command.

Here's what `mila init` needs to do:
- Setup the SSH config file:
    - Create an SSH config file with the correct permissions and all required entries.
    - Update the SSH config file if needed, preserving existing values if present.
- Setup SSH access to the cluster's login nodes:
    - If the user does not have an SSH public key:
        - create one (and let the user set a passphrase if they want)
        - (mila): Let them know that they need to end the public key to IT in the onboarding form.
        - (DRAC): Let them know that they need to add the public key via the web interface.
- Setup SSH access to the cluster's *compute* nodes:
    - Copy the SSH public key to the cluster's `authorized_keys` file.
        - (This seems to only be necessary to be able to connect to the Mila cluster's
           GPU Compute nodes at the moment, CPU nodes don't need this)
        - Setup the SSH directory and authorized_keys file with the correct permissions.
    - Create a SSH keypair on the cluster at ~/.ssh/id_rsa (with no passphrase) and add it to authorized_keys.
        - This is necessary to be able to connect to the compute node from a login node on the cluster.

Special cases:
- If the user is running `mila init` on Windows:
    - If WSL is installed, then tell them to re-run `mila init` from WSL.
    - If WSL is not installed, potentially install it for them, then re-run `mila init` from WSL?
- If the user is running on WSL and already has some things in their windows SSH directory, for instance an ssh keypair,
  then copy it to the WSL ssh directory instead of creating a new keypair.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pytest_mock import MockerFixture
from pytest_regressions.file_regression import FileRegressionFixture

from .init_command import (
    setup_compute_node_access,
    setup_login_node_access,
    setup_ssh_config,
)


class TestSetupSSHConfig:
    @pytest.mark.parametrize("mila_username", [None, "testuser_mila"])
    @pytest.mark.parametrize("drac_username", [None, "testuser_drac"])
    def test_create_ssh_config_file(
        self,
        tmp_path: Path,
        mila_username: str | None,
        drac_username: str | None,
        file_regression: FileRegressionFixture,
        mocker: MockerFixture,
    ):
        """Test that if there is no SSH config file, it gets created with all the
        expected entries."""
        ssh_dir = tmp_path / ".ssh"
        ssh_config_path = ssh_dir / "config"

        known_questions_to_answers = {
            "account on the Mila cluster": mila_username is not None,
            "username on the Mila cluster": mila_username,
            "account on the DRAC/ComputeCanada clusters": drac_username is not None,
            "username on the DRAC/ComputeCanada clusters": drac_username,
            "Is this OK?": True,
        }

        # This is a placeholder; in a real implementation, this would return the actual account name.
        def mocked_confirm(question: str, *args, **kwargs) -> bool:
            """Mocked prompt to return predefined answers for known questions."""
            for known_question, answer in known_questions_to_answers.items():
                if known_question in question:
                    return answer
            raise ValueError(f"Unexpected question: {question}")

        # This is a placeholder; in a real implementation, this would return the actual account name.
        def mocked_ask(question: str, *args, **kwargs) -> bool:
            """Mocked prompt to return predefined answers for known questions."""
            for known_question, answer in known_questions_to_answers.items():
                if known_question in question:
                    return answer
            raise ValueError(f"Unexpected question: {question}")

        _mock_confirm = mocker.patch(
            "rich.prompt.Confirm.ask", side_effect=mocked_confirm
        )
        _mock_ask = mocker.patch("rich.prompt.Prompt.ask", side_effect=mocked_ask)

        setup_ssh_config(ssh_config_path)

        # mock_confirm.assert_called()
        # mock_ask.assert_called()

        assert ssh_config_path.exists()
        file_regression.check(
            "Config file created from running `mila init`:\n"
            + "```\n"
            + ssh_config_path.read_text()
            + "```\n"
        )

    def test_update_ssh_config_file(self):
        """Test that the SSH config file is properly updated when a new cluster is
        added."""
        raise NotImplementedError("TODO")

    def test_errors_if_no_public_key_present(self):
        """Test that an error is raised and a helpful message is displayed if there
        isn't an SSH public key in the users's SSH directory."""
        raise NotImplementedError("TODO")


class TestSetupLoginNodeAccess:
    def test_creates_ssh_keypair(self):
        setup_login_node_access("mila")
        raise NotImplementedError("TODO")


class TestSetupComputeNodeAccess:
    def test_copies_ssh_public_key_to_remote_authorized_keys(self):
        """Test that the local SSH public key is copied to the remote authorized_keys
        file."""
        setup_compute_node_access("mila")
        raise NotImplementedError("TODO")
