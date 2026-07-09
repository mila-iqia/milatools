"""Forwards the SLURM allocation environment to VS Code terminals.

VS Code's Remote-SSH extension connects to the compute node in a fresh SSH
session, outside the salloc/srun job. `pam_slurm_adopt` adopts that session
into the job's cgroup but only sets `SLURM_JOB_ID` — none of the other
`SLURM_*` variables (`SLURM_NTASKS`, `SLURM_TASKS_PER_NODE`, `SLURM_TMPDIR`,
...).

To fix this, when `mila code` creates a job, the *allocation-level* SLURM
environment (as seen by the salloc shell or the sbatch batch script — NOT the
environment of a 1-task job step, which would have the wrong task geometry) is
dumped to a per-job file on the shared filesystem. A guarded block installed
in the user's shell rc file then sources that file in sessions that have
`SLURM_JOB_ID` but not the rest of the job environment.
"""

from __future__ import annotations

import asyncio
import importlib.resources

from milatools.cli import console
from milatools.utils.remote_v2 import RemoteV2, logger

_RESOURCES = importlib.resources.files("milatools.utils")

# NOTE: A literal `$HOME` (expanded remotely) so that the dump script, the rc
# hook and the commands below all compute the exact same path.
SLURM_ENV_DIR = "$HOME/.cache/milatools/slurm_env"

DUMP_SCRIPT_PATH = f"{SLURM_ENV_DIR}/dump.sh"


def env_file_path(job_id: int) -> str:
    """Path (on the cluster) of the env file for the given job."""
    return f"{SLURM_ENV_DIR}/{job_id}.env"


# Command used to run the dump script from within the job (from the salloc
# shell's stdin or the sbatch --wrap script). Only uses syntax that is valid in
# bash, zsh and fish, is silent, and is a no-op if the script was never
# uploaded.
DUMP_COMMAND = f'test -f "{DUMP_SCRIPT_PATH}" && sh "{DUMP_SCRIPT_PATH}"'

# POSIX-sh script uploaded to DUMP_SCRIPT_PATH on the cluster.
# It writes the SLURM_* variables of the current environment as safely-quoted
# `export VAR='...'` lines to the per-job env file (allocation-level variables
# only; see the comments in the script itself).
DUMP_SCRIPT_CONTENT = (_RESOURCES / "slurm_env_dump.sh").read_text(encoding="utf-8")

_RC_BLOCK_VERSION = 1
RC_BLOCK_PREFIX = "# >>> milatools slurm-env"
RC_BLOCK_START = f"{RC_BLOCK_PREFIX} v{_RC_BLOCK_VERSION} >>>"
RC_BLOCK_END = "# <<< milatools slurm-env <<<"

# Block installed in ~/.bashrc (and ~/.zshrc if it exists) on the cluster.
# `_content_with_block_installed` replaces an installed block whenever its
# content differs from the current RC_BLOCK, so any change to the .sh file (or
# to `_RC_BLOCK_VERSION` above, kept as a human-readable marker of such
# changes) gets rolled out to existing rc files.
# Guards (all pure POSIX, valid in bash and zsh):
# - `SLURM_JOB_ID`/`SLURM_JOBID` set: only sessions inside a job are affected
#   (login nodes and regular SSH sessions are untouched);
# - `SLURM_NTASKS` and `SLURM_STEP_ID` unset: real salloc/srun shells and the
#   user's own job scripts already have the full environment and are never
#   clobbered;
# - the per-job env file exists: only jobs created by `mila code` have one.
RC_BLOCK = (_RESOURCES / "slurm_env_rc_block.sh").read_text(encoding="utf-8")
# NOTE: The file content already ends with a newline.
RC_BLOCK = f"""{RC_BLOCK_START}
{RC_BLOCK}{RC_BLOCK_END}
"""


def _content_with_block_installed(existing: str | None) -> str | None:
    """Returns the new content of an rc file with the current RC_BLOCK installed.

    Returns None if the file already contains the current version of the block.
    An outdated block (different version between the same markers) is replaced
    in place; otherwise the block is appended.
    """
    if existing is not None and RC_BLOCK.strip() in existing:
        return None
    if existing:
        start = existing.find(RC_BLOCK_PREFIX)
        if start != -1:
            end = existing.find(RC_BLOCK_END, start)
            if end != -1:
                end += len(RC_BLOCK_END)
                # Also consume the newline following the end marker, if any.
                if existing[end : end + 1] == "\n":
                    end += 1
                return existing[:start] + RC_BLOCK + existing[end:]
    if not existing:
        return RC_BLOCK
    if not existing.endswith("\n"):
        existing += "\n"
    return existing + "\n" + RC_BLOCK


async def _install_block_in_rc_file(
    login_node: RemoteV2, rc_file: str, only_if_exists: bool = False
) -> None:
    result = await login_node.run_async(
        f"cat {rc_file}", display=False, warn=True, hide=True
    )
    if result.returncode not in (0, 1):
        # Exit code 1 means the file doesn't exist; anything else (e.g. 255 for
        # an SSH failure) means we can't know its content, and treating it as
        # missing would overwrite the user's rc file with just the block.
        logger.warning(
            f"Could not read {rc_file} on {login_node.hostname} (exit code "
            f"{result.returncode}); not installing the milatools block in it."
        )
        return
    existing = result.stdout if result.returncode == 0 else None
    if existing is None and only_if_exists:
        return
    new_content = _content_with_block_installed(existing)
    if new_content is None:
        logger.debug(f"The milatools block in {rc_file} is already up to date.")
        return
    console.log(
        f"Adding a block to {rc_file} on {login_node.hostname} so that VS Code "
        "terminals get the SLURM environment variables of the job.",
        style="cyan",
    )
    await login_node.run_async(
        f"cat > {rc_file}.milatools.tmp && mv {rc_file}.milatools.tmp {rc_file}",
        input=new_content,
        display=False,
        hide=True,
    )


async def setup_slurm_env_hook(login_node: RemoteV2) -> None:
    """Sets up what's needed for VS Code terminals to get the job's SLURM env.

    - Uploads the env dump script (run from inside new jobs) to the cluster;
    - Installs the rc block that sources the per-job env file in adopted
      sessions into `~/.bashrc` (and `~/.zshrc`, only if it already exists);
    - Prunes env files older than the maximum job duration.

    Everything is best-effort: failures are logged as warnings and never
    prevent `mila code` from working.
    """
    try:
        existing_script = await login_node.run_async(
            f'cat "{DUMP_SCRIPT_PATH}"', display=False, warn=True, hide=True
        )
        if existing_script.stdout != DUMP_SCRIPT_CONTENT:
            logger.debug(f"Uploading the SLURM env dump script to {DUMP_SCRIPT_PATH}.")
            await login_node.run_async(
                f'mkdir -p "{SLURM_ENV_DIR}" && cat > "{DUMP_SCRIPT_PATH}"',
                input=DUMP_SCRIPT_CONTENT,
                display=False,
                hide=True,
            )

        await _install_block_in_rc_file(login_node, "~/.bashrc")
        await _install_block_in_rc_file(login_node, "~/.zshrc", only_if_exists=True)

        # Prune env files of jobs that have necessarily ended (`mila code`
        # jobs run for at most 7 days). Files of running jobs are removed in
        # `ComputeNode.close`/`close_async` when the job is cancelled.
        await login_node.run_async(
            f'find "{SLURM_ENV_DIR}" -name "*.env" -mtime +8 -delete',
            display=False,
            warn=True,
            hide=True,
        )
    except Exception as err:
        logger.warning(
            f"Unable to set up the SLURM env forwarding for VS Code terminals: {err}"
        )


async def wait_for_env_file(
    login_node: RemoteV2, job_id: int, timeout: float = 15.0
) -> bool:
    """Waits until the env file for the given job exists on the cluster.

    Returns False (after logging a warning) if the file doesn't show up within
    `timeout` seconds.
    """
    try:
        dump_script_exists = await login_node.run_async(
            f'test -f "{DUMP_SCRIPT_PATH}"', display=False, warn=True, hide=True
        )
        if dump_script_exists.returncode != 0:
            # `setup_slurm_env_hook` wasn't run (or failed): the job can't dump
            # its env, no point in waiting for the file.
            logger.debug(
                "The SLURM env dump script isn't on the cluster; not waiting "
                "for an env file."
            )
            return False
        interval = 0.5
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            result = await login_node.run_async(
                f'test -f "{env_file_path(job_id)}"',
                display=False,
                warn=True,
                hide=True,
            )
            if result.returncode == 0:
                return True
            await asyncio.sleep(interval)
            interval = min(interval * 1.5, 2.0)
    except Exception as err:
        logger.warning(f"Error while waiting for the SLURM env file: {err}")
        return False
    logger.warning(
        f"Timed out waiting for the SLURM env file of job {job_id}; VS Code "
        "terminals may be missing some SLURM_* environment variables."
    )
    return False


async def remove_env_file(login_node: RemoteV2, job_id: int) -> None:
    """Removes the env file of the given job (best-effort)."""
    try:
        await login_node.run_async(
            f'rm -f "{env_file_path(job_id)}"', display=False, warn=True, hide=True
        )
    except Exception as err:
        logger.debug(f"Unable to remove the SLURM env file of job {job_id}: {err}")


def remove_env_file_sync(login_node: RemoteV2, job_id: int) -> None:
    """Synchronous version of `remove_env_file`."""
    try:
        login_node.run(
            f'rm -f "{env_file_path(job_id)}"', display=False, warn=True, hide=True
        )
    except Exception as err:
        logger.debug(f"Unable to remove the SLURM env file of job {job_id}: {err}")
