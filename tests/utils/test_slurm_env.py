from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from pytest_regressions.file_regression import FileRegressionFixture

from milatools.utils.remote_v2 import RemoteV2
from milatools.utils.slurm_env import (
    DUMP_SCRIPT_CONTENT,
    DUMP_SCRIPT_PATH,
    RC_BLOCK,
    RC_BLOCK_END,
    RC_BLOCK_START,
    SLURM_ENV_DIR,
    _content_with_block_installed,
    env_file_path,
    remove_env_file,
    remove_env_file_sync,
    setup_slurm_env_hook,
    wait_for_env_file,
)

JOB_ID = 1234

ALLOCATION_ENV = {
    "SLURM_JOB_ID": str(JOB_ID),
    "SLURM_NTASKS": "4",
    "SLURM_NNODES": "2",
    "SLURM_TASKS_PER_NODE": "3,1",
    "SLURM_JOB_NODELIST": "cn-f[001-002]",
    "SLURM_TMPDIR": f"/Tmp/slurm.{JOB_ID}.0",
    # A value with spaces and single quotes, to test the quoting.
    "SLURM_JOB_NAME": "it's a 'test' job",
}
# Not step-specific, but still excluded from the dump: these are documented
# `sbatch`/`salloc` "Input Environment Variables" (see
# https://slurm.schedmd.com/sbatch.html#SECTION_INPUT-ENVIRONMENT-VARIABLES
# and https://slurm.schedmd.com/salloc.html#SECTION_INPUT-ENVIRONMENT-VARIABLES),
# not job state, and would silently change the behavior of sbatch/salloc/srun
# run from the terminal if they leaked into it.
EXCLUDED_ENV = {
    "SLURM_EXPORT_ENV": "ALL",
    "SLURM_CONF": "/etc/slurm/slurm.conf",
    "SLURM_CLUSTERS": "some-other-cluster",
    "SLURM_HINT": "compute_bound",
    "SLURM_DEBUG_FLAGS": "Steps",
    "SLURM_EXIT_ERROR": "63",
    "SLURM_EXIT_IMMEDIATE": "64",
}
STEP_ENV = {
    "SLURM_STEP_ID": "0",
    "SLURM_STEPID": "0",
    "SLURM_PROCID": "0",
    "SLURM_LOCALID": "0",
    "SLURM_NODEID": "0",
    "SLURM_GTIDS": "0",
    "SLURM_TASK_PID": "12345",
    "SLURM_LAUNCH_NODE_IPADDR": "10.0.0.1",
    "SLURM_SRUN_COMM_HOST": "10.0.0.1",
    "SLURM_CPU_BIND": "quiet,mask_cpu:0x3",
    "SLURM_DISTRIBUTION": "cyclic",
    "SLURM_PTY_PORT": "12346",
    "SLURM_TOPOLOGY_ADDR": "cn-f001",
    "SLURM_UMASK": "0022",
    "SLURMD_NODENAME": "cn-f001",
}


def _run_dump_script(home: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["sh", "-c", DUMP_SCRIPT_CONTENT],
        env={"HOME": str(home), "PATH": os.environ["PATH"], **env},
        capture_output=True,
        text=True,
    )


def _local_env_file(home: Path, job_id: int = JOB_ID) -> Path:
    return home / ".cache/milatools/slurm_env" / f"{job_id}.env"


def test_dump_script_writes_the_allocation_env(tmp_path: Path):
    result = _run_dump_script(tmp_path, {**ALLOCATION_ENV, **STEP_ENV, **EXCLUDED_ENV})
    assert result.returncode == 0, result.stderr

    env_file = _local_env_file(tmp_path)
    assert env_file.exists()

    # Sourcing the file in a fresh shell reproduces the allocation env exactly
    # (and nothing else).
    sourced_env_lines = subprocess.run(
        ["sh", "-c", f". '{env_file}'; printenv | grep '^SLURM'"],
        env={"PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    sourced_env = dict(line.split("=", maxsplit=1) for line in sourced_env_lines)
    assert sourced_env == ALLOCATION_ENV


def test_dump_script_does_nothing_outside_a_job(tmp_path: Path):
    result = _run_dump_script(tmp_path, {})
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / ".cache").exists()


shells = (
    pytest.param("bash"),
    pytest.param(
        "zsh",
        marks=pytest.mark.skipif(not shutil.which("zsh"), reason="zsh isn't installed"),
    ),
)


@pytest.mark.parametrize("shell", shells)
@pytest.mark.parametrize(
    ("session_env", "expected_ntasks"),
    [
        # A pam_slurm_adopt-adopted session (e.g. VS Code terminal): only the
        # job id is set -> the env file should be sourced.
        ({"SLURM_JOB_ID": str(JOB_ID)}, "4"),
        # Same, with the alternate spelling of the job id variable.
        ({"SLURM_JOBID": str(JOB_ID)}, "4"),
        # Login node / laptop: no SLURM vars at all -> inert.
        ({}, "unset"),
        # A real salloc/srun shell already has the full env -> never clobbered.
        ({"SLURM_JOB_ID": str(JOB_ID), "SLURM_NTASKS": "1"}, "1"),
        ({"SLURM_JOB_ID": str(JOB_ID), "SLURM_STEP_ID": "0"}, "unset"),
        # A job that milatools didn't create: no env file -> inert.
        ({"SLURM_JOB_ID": str(JOB_ID + 1)}, "unset"),
    ],
    ids=[
        "adopted_session",
        "adopted_session_alt_spelling",
        "no_job",
        "real_shell_with_ntasks",
        "real_step_shell",
        "foreign_job",
    ],
)
def test_rc_block_guards(
    tmp_path: Path, shell: str, session_env: dict[str, str], expected_ntasks: str
):
    # Create the env file for JOB_ID, as the dump script would.
    result = _run_dump_script(tmp_path, ALLOCATION_ENV)
    assert result.returncode == 0, result.stderr
    rc_file = tmp_path / "rcfile"
    rc_file.write_text(RC_BLOCK)

    output = subprocess.run(
        [shell, "-c", f". '{rc_file}'; echo \"${{SLURM_NTASKS:-unset}}\""],
        env={"HOME": str(tmp_path), "PATH": os.environ["PATH"], **session_env},
        capture_output=True,
        text=True,
    )
    assert output.returncode == 0, output.stderr
    assert output.stdout.strip() == expected_ntasks


def _setup_terminal(tmp_path: Path, sbatch_exit_code: int = 0) -> Path:
    """Sets up what an adopted VS Code terminal session needs: the per-job env
    file (via the dump script), an rc file with the block, and a fake `sbatch`
    on PATH that prints the SLURM_*/MY_VAR variables of its environment and
    its arguments. Returns the fake bin directory."""
    result = _run_dump_script(tmp_path, ALLOCATION_ENV)
    assert result.returncode == 0, result.stderr
    (tmp_path / "rcfile").write_text(RC_BLOCK)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_sbatch = bin_dir / "sbatch"
    fake_sbatch.write_text(
        "#!/bin/sh\n"
        "printenv | grep -e '^SLURM_' -e '^MY_VAR'\n"
        'echo "args: $@"\n'
        f"exit {sbatch_exit_code}\n"
    )
    fake_sbatch.chmod(0o755)
    return bin_dir


def _run_terminal_command(
    shell: str,
    tmp_path: Path,
    bin_dir: Path,
    command: str,
    session_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    if session_env is None:
        session_env = {"SLURM_JOB_ID": str(JOB_ID)}
    return subprocess.run(
        [shell, "-c", f". '{tmp_path / 'rcfile'}'; {command}"],
        env={
            "HOME": str(tmp_path),
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            **session_env,
        },
        capture_output=True,
        text=True,
    )


def _slurm_vars_in(output: str) -> list[str]:
    return [line for line in output.splitlines() if line.startswith("SLURM_")]


@pytest.mark.parametrize("shell", shells)
def test_sbatch_wrapper_submits_with_a_clean_env(tmp_path: Path, shell: str):
    """`sbatch` from an adopted session must not leak the restored job env into
    the submitted job, while the user's own environment and arguments pass
    through, and the terminal keeps the job env afterwards."""
    bin_dir = _setup_terminal(tmp_path)
    output = _run_terminal_command(
        shell,
        tmp_path,
        bin_dir,
        "export MY_VAR=hello; sbatch --ntasks=2 job.sh;"
        ' echo "after: ${SLURM_NTASKS:-unset}"',
    )
    assert output.returncode == 0, output.stderr
    assert _slurm_vars_in(output.stdout) == []
    assert "MY_VAR=hello" in output.stdout
    assert "args: --ntasks=2 job.sh" in output.stdout
    # The wrapper's unsets are contained in a subshell: the terminal itself
    # keeps the job's environment (needed by `srun`, user scripts, ...).
    assert "after: 4" in output.stdout


@pytest.mark.parametrize("shell", shells)
def test_sbatch_wrapper_propagates_the_exit_code(tmp_path: Path, shell: str):
    bin_dir = _setup_terminal(tmp_path, sbatch_exit_code=3)
    output = _run_terminal_command(shell, tmp_path, bin_dir, "sbatch job.sh")
    assert output.returncode == 3, output.stderr


@pytest.mark.parametrize("shell", shells)
def test_sbatch_wrapper_with_hostile_ifs(tmp_path: Path, shell: str):
    """A custom IFS in the terminal must not defeat the cleanup (word
    splitting of the variable-name list depends on IFS)."""
    bin_dir = _setup_terminal(tmp_path)
    output = _run_terminal_command(shell, tmp_path, bin_dir, "IFS=:; sbatch job.sh")
    assert output.returncode == 0, output.stderr
    assert _slurm_vars_in(output.stdout) == []


@pytest.mark.parametrize("shell", shells)
def test_sbatch_wrapper_under_set_u(tmp_path: Path, shell: str):
    bin_dir = _setup_terminal(tmp_path)
    output = _run_terminal_command(shell, tmp_path, bin_dir, "set -u; sbatch job.sh")
    assert output.returncode == 0, output.stderr
    assert _slurm_vars_in(output.stdout) == []


@pytest.mark.parametrize("shell", shells)
def test_sbatch_wrapper_preserves_slurm_conf(tmp_path: Path, shell: str):
    """Env files dumped by older milatools versions may contain SLURM_CONF
    (cluster configuration): the wrapper must never unset it, or sbatch
    couldn't find the cluster config at all."""
    bin_dir = _setup_terminal(tmp_path)
    with _local_env_file(tmp_path).open("a") as f:
        f.write("export SLURM_CONF='/etc/slurm/slurm.conf'\n")
    output = _run_terminal_command(shell, tmp_path, bin_dir, "sbatch job.sh")
    assert output.returncode == 0, output.stderr
    assert _slurm_vars_in(output.stdout) == ["SLURM_CONF=/etc/slurm/slurm.conf"]


@pytest.mark.parametrize("shell", shells)
def test_rc_block_parses_with_a_preexisting_sbatch_alias(tmp_path: Path, shell: str):
    """A user-defined `sbatch` alias earlier in the rc file must not break
    sourcing the block (in zsh, aliases expand while *parsing* a function
    definition, so the POSIX `sbatch() {...}` form would be a parse error that
    aborts the rest of the rc file)."""
    bin_dir = _setup_terminal(tmp_path)
    rc_file = tmp_path / "rcfile"
    rc_file.write_text("alias sbatch='sbatch --partition=main'\n" + RC_BLOCK)
    output = _run_terminal_command(shell, tmp_path, bin_dir, "echo sourced-ok")
    assert output.returncode == 0, output.stderr
    assert "sourced-ok" in output.stdout
    assert "parse error" not in output.stderr


@pytest.mark.parametrize("shell", shells)
def test_sbatch_is_not_wrapped_in_real_job_shells(tmp_path: Path, shell: str):
    """In a real salloc/srun shell the block never sources the env file, so
    `sbatch` must behave exactly as it normally does there (env untouched)."""
    bin_dir = _setup_terminal(tmp_path)
    output = _run_terminal_command(
        shell,
        tmp_path,
        bin_dir,
        "sbatch job.sh",
        session_env={"SLURM_JOB_ID": str(JOB_ID), "SLURM_NTASKS": "1"},
    )
    assert output.returncode == 0, output.stderr
    assert "SLURM_NTASKS=1" in output.stdout


class TestContentWithBlockInstalled:
    def test_missing_or_empty_file(self):
        assert _content_with_block_installed(None) == RC_BLOCK
        assert _content_with_block_installed("") == RC_BLOCK

    def test_appends_to_existing_content(self):
        existing = "# my stuff\nalias ll='ls -l'\n"
        new_content = _content_with_block_installed(existing)
        assert new_content is not None
        assert new_content.startswith(existing)
        assert new_content.endswith(RC_BLOCK)

    def test_adds_missing_final_newline(self):
        new_content = _content_with_block_installed("# no final newline")
        assert new_content is not None
        assert new_content.startswith("# no final newline\n")
        assert new_content.endswith(RC_BLOCK)

    def test_up_to_date_block_is_left_alone(self):
        existing = "# my stuff\n\n" + RC_BLOCK
        assert _content_with_block_installed(existing) is None

    def test_outdated_block_is_replaced_in_place(
        self, file_regression: FileRegressionFixture
    ):
        outdated_block = RC_BLOCK.replace("v2", "v1").replace(
            "This restores", "(old text) This restores"
        )
        existing = f"# before the block\n\n{outdated_block}\n# after the block\n"
        new_content = _content_with_block_installed(existing)
        assert new_content is not None
        assert new_content.startswith("# before the block\n")
        assert new_content.endswith("# after the block\n")
        assert RC_BLOCK in new_content
        assert "v1" not in new_content
        file_regression.check(new_content)

    def test_result_is_idempotent(self):
        new_content = _content_with_block_installed("# my stuff\n")
        assert new_content is not None
        assert _content_with_block_installed(new_content) is None


class _FakeCluster:
    """Backs a mocked RemoteV2 with a dict-based fake filesystem.

    Understands only the commands issued by the functions under test.
    """

    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.write_commands: list[str] = []

    def run(self, command: str, *args, input: str | None = None, **kwargs):
        if command.startswith("cat > "):
            # `cat > {tmp} && mv {tmp} {file}` or `mkdir -p {dir} && cat > {file}`
            assert input is not None
            target = command.split()[-1]
            self.files[target] = input
            self.write_commands.append(command)
            return subprocess.CompletedProcess(command, 0, "", "")
        if command.startswith("mkdir -p ") and "cat > " in command:
            assert input is not None
            target = command.split()[-1].strip('"')
            self.files[target] = input
            self.write_commands.append(command)
            return subprocess.CompletedProcess(command, 0, "", "")
        if command.startswith("cat "):
            path = command.removeprefix("cat ").strip('"')
            if path in self.files:
                return subprocess.CompletedProcess(command, 0, self.files[path], "")
            return subprocess.CompletedProcess(command, 1, "", "No such file")
        if command.startswith("find "):
            return subprocess.CompletedProcess(command, 0, "", "")
        if command.startswith("test -f "):
            path = command.removeprefix("test -f ").strip('"')
            returncode = 0 if path in self.files else 1
            return subprocess.CompletedProcess(command, returncode, "", "")
        if command.startswith("rm -f "):
            path = command.removeprefix("rm -f ").strip('"')
            self.files.pop(path, None)
            self.write_commands.append(command)
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(f"Unexpected command: {command}")

    def mock_login_node(self) -> Mock:
        async def _run_async(command: str, *args, **kwargs):
            return self.run(command, *args, **kwargs)

        mock_login_node = Mock(spec=RemoteV2, hostname="mila")
        mock_login_node.configure_mock(
            run=Mock(spec=RemoteV2.run, side_effect=self.run),
            run_async=AsyncMock(spec=RemoteV2.run_async, side_effect=_run_async),
        )
        return mock_login_node


@pytest.mark.asyncio
async def test_setup_slurm_env_hook():
    fake_cluster = _FakeCluster()
    fake_cluster.files["~/.zshrc"] = "# my zsh stuff\n"
    login_node = fake_cluster.mock_login_node()

    await setup_slurm_env_hook(login_node)

    assert fake_cluster.files[DUMP_SCRIPT_PATH] == DUMP_SCRIPT_CONTENT
    # ~/.bashrc was created with the block, the pre-existing ~/.zshrc got it
    # appended.
    assert fake_cluster.files["~/.bashrc"] == RC_BLOCK
    assert fake_cluster.files["~/.zshrc"].startswith("# my zsh stuff\n")
    assert fake_cluster.files["~/.zshrc"].endswith(RC_BLOCK)


@pytest.mark.asyncio
async def test_setup_slurm_env_hook_does_not_create_zshrc():
    fake_cluster = _FakeCluster()
    await setup_slurm_env_hook(fake_cluster.mock_login_node())
    assert "~/.zshrc" not in fake_cluster.files


@pytest.mark.asyncio
async def test_setup_slurm_env_hook_is_idempotent():
    fake_cluster = _FakeCluster()
    login_node = fake_cluster.mock_login_node()
    await setup_slurm_env_hook(login_node)
    files_after_first_call = dict(fake_cluster.files)
    fake_cluster.write_commands.clear()

    await setup_slurm_env_hook(login_node)

    assert fake_cluster.write_commands == []
    assert fake_cluster.files == files_after_first_call


@pytest.mark.asyncio
async def test_setup_slurm_env_hook_never_raises():
    login_node = Mock(spec=RemoteV2, hostname="mila")
    login_node.configure_mock(
        run_async=AsyncMock(
            spec=RemoteV2.run_async, side_effect=RuntimeError("connection lost")
        ),
    )
    # Should log a warning, not raise.
    await setup_slurm_env_hook(login_node)


@pytest.mark.asyncio
async def test_unreadable_rc_file_is_not_overwritten():
    """An rc file that can't be read (e.g. exit code 255 on a transient SSH
    failure) must not be treated as missing, which would overwrite it with just
    the block."""
    fake_cluster = _FakeCluster()
    fake_cluster.files["~/.bashrc"] = "# my precious bashrc\n"
    login_node = fake_cluster.mock_login_node()

    async def _run_async(command: str, *args, **kwargs):
        if command == "cat ~/.bashrc":
            return subprocess.CompletedProcess(command, 255, "", "connection lost")
        return fake_cluster.run(command, *args, **kwargs)

    login_node.run_async.side_effect = _run_async

    await setup_slurm_env_hook(login_node)

    assert fake_cluster.files["~/.bashrc"] == "# my precious bashrc\n"


@pytest.mark.asyncio
async def test_wait_for_env_file():
    fake_cluster = _FakeCluster()
    fake_cluster.files[DUMP_SCRIPT_PATH] = DUMP_SCRIPT_CONTENT
    login_node = fake_cluster.mock_login_node()

    assert not await wait_for_env_file(login_node, JOB_ID, timeout=1.0)

    fake_cluster.files[env_file_path(JOB_ID)] = "export SLURM_NTASKS='4'\n"
    assert await wait_for_env_file(login_node, JOB_ID, timeout=1.0)


@pytest.mark.asyncio
async def test_wait_for_env_file_returns_early_without_the_dump_script():
    """No need to wait for an env file if the dump script isn't on the cluster."""
    fake_cluster = _FakeCluster()
    fake_cluster.files[env_file_path(JOB_ID)] = "export SLURM_NTASKS='4'\n"
    login_node = fake_cluster.mock_login_node()

    assert not await wait_for_env_file(login_node, JOB_ID, timeout=30.0)
    # Only the check for the dump script was made (no polling for the env file).
    login_node.run_async.assert_awaited_once()


@pytest.mark.asyncio
async def test_wait_for_env_file_does_not_raise():
    login_node = Mock(spec=RemoteV2, hostname="mila")
    login_node.configure_mock(
        run_async=AsyncMock(
            spec=RemoteV2.run_async, side_effect=RuntimeError("connection lost")
        ),
    )
    assert not await wait_for_env_file(login_node, JOB_ID, timeout=1.0)


@pytest.mark.asyncio
async def test_remove_env_file():
    fake_cluster = _FakeCluster()
    fake_cluster.files[env_file_path(JOB_ID)] = "export SLURM_NTASKS='4'\n"
    login_node = fake_cluster.mock_login_node()

    await remove_env_file(login_node, JOB_ID)
    assert env_file_path(JOB_ID) not in fake_cluster.files

    fake_cluster.files[env_file_path(JOB_ID)] = "export SLURM_NTASKS='4'\n"
    remove_env_file_sync(login_node, JOB_ID)
    assert env_file_path(JOB_ID) not in fake_cluster.files


def test_sh_files_match_the_python_side_markers():
    """The .sh files are loaded as package data; make sure their content stays
    consistent with the constants used by the Python code to locate them."""
    assert DUMP_SCRIPT_CONTENT.startswith("#!/bin/sh\n")
    # `_content_with_block_installed` finds installed (possibly outdated)
    # blocks with these markers.
    assert RC_BLOCK.startswith(RC_BLOCK_START + "\n")
    assert RC_BLOCK.endswith(RC_BLOCK_END + "\n")


def test_paths_are_home_relative():
    """The script, hook and Python code must all compute the same paths, so
    they are all based on a literal `$HOME` (expanded on the cluster)."""
    assert SLURM_ENV_DIR.startswith("$HOME/")
    assert DUMP_SCRIPT_PATH.startswith(f"{SLURM_ENV_DIR}/")
    assert env_file_path(JOB_ID) == f"{SLURM_ENV_DIR}/{JOB_ID}.env"
    assert "$HOME/.cache/milatools/slurm_env" in RC_BLOCK
    assert '"$HOME/.cache/milatools/slurm_env"' in DUMP_SCRIPT_CONTENT
