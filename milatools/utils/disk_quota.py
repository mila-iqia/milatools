from __future__ import annotations

from logging import getLogger as get_logger

from typing_extensions import deprecated

from milatools.cli import console
from milatools.cli.utils import MilatoolsUserError, T
from milatools.utils.remote_v1 import RemoteV1, SlurmRemote
from milatools.utils.remote_v2 import RemoteV2

logger = get_logger(__name__)


async def check_disk_quota(remote: RemoteV2) -> None:
    """Checks that the disk quota isn't exceeded on the remote $HOME filesystem."""
    # NOTE: This is what the output of the command looks like on the Mila cluster:
    #
    # Disk quotas for usr normandf (uid 1471600598):
    #      Filesystem  kbytes   quota   limit   grace   files   quota   limit   grace
    # /home/mila/n/normandf
    #                 95747836       0 104857600       -  908722       0 1048576       -
    # uid 1471600598 is using default block quota setting
    # uid 1471600598 is using default file quota setting

    # Need to assert this, otherwise .get_output calls .run which would spawn a job!
    if not (await remote.get_output_async("which lfs", display=False, hide=True)):
        logger.debug("Cluster doesn't have the lfs command. Skipping check.")
        return
    console.log("Checking disk quota on $HOME...")
    home_disk_quota_output = await remote.get_output_async(
        "lfs quota -u $USER $HOME", display=False, hide=True
    )
    _check_disk_quota_common_part(home_disk_quota_output, cluster=remote.hostname)


@deprecated("Deprecated: use `check_disk_quota` instead. ", category=None)
def check_disk_quota_v1(remote: RemoteV1 | RemoteV2) -> None:
    """Checks that the user's disk quota isn't exceeded on the remote filesystem(s)."""
    # Need to check for this, because SlurmRemote is a subclass of RemoteV1 and
    # .get_output calls SlurmRemote.run which would spawn a job!
    assert not isinstance(remote, SlurmRemote)
    if not (remote.get_output("which lfs", display=False, hide=True)):
        logger.debug("Cluster doesn't have the lfs command. Skipping check.")
        return
    console.log("Checking disk quota on $HOME...")
    home_disk_quota_output = remote.get_output(
        "lfs quota -u $USER $HOME", display=False, hide=True
    )
    _check_disk_quota_common_part(home_disk_quota_output, cluster=remote.hostname)


def _parse_lfs_quota_output(
    lfs_quota_output: str,
) -> tuple[tuple[float, float], tuple[int, int]]:
    """Parses space and # of files (usage, limit) from the  output of `lfs quota`."""
    lines = lfs_quota_output.splitlines()

    header_line: str | None = None
    header_line_index: int | None = None
    for index, line in enumerate(lines):
        if (
            len(line_parts := line.strip().split()) == 9
            and line_parts[0].lower() == "filesystem"
        ):
            header_line = line
            header_line_index = index
            break
    assert header_line
    assert header_line_index is not None

    values_line_parts: list[str] = []
    # The next line may overflow to two (or maybe even more?) lines if the name of the
    # $HOME dir is too long.
    for content_line in lines[header_line_index + 1 :]:
        additional_values = content_line.strip().split()
        assert len(values_line_parts) < 9
        values_line_parts.extend(additional_values)
        if len(values_line_parts) == 9:
            break

    assert len(values_line_parts) == 9, values_line_parts
    (
        _filesystem,
        used_kbytes,
        _quota_kbytes,
        limit_kbytes,
        _grace_kbytes,
        files,
        _quota_files,
        limit_files,
        _grace_files,
    ) = values_line_parts

    used_gb = int(used_kbytes.strip()) / (1024**2)
    max_gb = int(limit_kbytes.strip()) / (1024**2)
    used_files = int(files.strip())
    max_files = int(limit_files.strip())
    return (used_gb, max_gb), (used_files, max_files)


def _check_disk_quota_common_part(home_disk_quota_output: str, cluster: str):
    if "not on a mounted Lustre filesystem" in home_disk_quota_output:
        logger.debug("Cluster doesn't use lustre on $HOME filesystem. Skipping check.")
        return

    (used_gb, max_gb), (used_files, max_files) = _parse_lfs_quota_output(
        home_disk_quota_output
    )

    def get_colour(used: float, max: float) -> str:
        return "red" if used >= max else "orange" if used / max > 0.7 else "green"

    disk_usage_style = get_colour(used_gb, max_gb)
    num_files_style = get_colour(used_files, max_files)
    from rich.text import Text

    console.log(
        "Disk usage:",
        Text(f"{used_gb:.2f} / {max_gb:.2f} GiB", style=disk_usage_style),
        "and",
        Text(f"{used_files} / {max_files} files", style=num_files_style),
        markup=False,
    )
    size_ratio = used_gb / max_gb
    files_ratio = used_files / max_files
    reason = (
        f"{used_gb:.1f} / {max_gb} GiB"
        if size_ratio > files_ratio
        else f"{used_files} / {max_files} files"
    )

    freeing_up_space_instructions = (
        "For example, temporary files (logs, checkpoints, etc.) can be moved to "
        "$SCRATCH, while files that need to be stored for longer periods can be moved "
        "to $ARCHIVE or to a shared project folder under /network/projects.\n"
        "Visit https://docs.mila.quebec/Information.html#storage to learn more about "
        "how to best make use of the different filesystems available on the cluster."
    )

    if used_gb >= max_gb or used_files >= max_files:
        raise MilatoolsUserError(
            T.red(
                f"ERROR: Your disk quota on the $HOME filesystem is exceeded! "
                f"({reason}).\n"
                f"To fix this, login to the cluster with `ssh {cluster}` and free up "
                f"some space, either by deleting files, or by moving them to a "
                f"suitable filesystem.\n" + freeing_up_space_instructions
            )
        )
    if max(size_ratio, files_ratio) > 0.9:
        warning_message = (
            f"You are getting pretty close to your disk quota on the $HOME "
            f"filesystem: ({reason})\n"
            "Please consider freeing up some space in your $HOME folder, either by "
            "deleting files, or by moving them to a more suitable filesystem.\n"
            + freeing_up_space_instructions
        )
        logger.warning(UserWarning(warning_message))
