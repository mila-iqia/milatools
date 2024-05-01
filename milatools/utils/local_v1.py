from __future__ import annotations

import shlex
import socket
import subprocess
import sys
from logging import getLogger as get_logger
from subprocess import CompletedProcess
from typing import IO, Any

import fabric
import paramiko.ssh_exception
from typing_extensions import deprecated

from milatools.cli.utils import CommandNotFoundError, T, cluster_to_connect_kwargs
from milatools.utils.remote_v2 import SSH_CONFIG_FILE, is_already_logged_in

logger = get_logger(__name__)


@deprecated("LocalV1 is being deprecated. Use LocalV2 instead.", category=None)
class LocalV1:
    def display(self, args: list[str] | tuple[str, ...]) -> None:
        display(args)

    def silent_get(self, *cmd: str) -> str:
        return subprocess.check_output(cmd, universal_newlines=True)

    @deprecated("This isn't used and will probably be removed. Don't start using it.")
    def get(self, *cmd: str) -> str:
        display(cmd)
        return subprocess.check_output(cmd, universal_newlines=True)

    def run(
        self,
        *cmd: str,
        stdout: int | IO[Any] | None = None,
        stderr: int | IO[Any] | None = None,
        capture_output: bool = False,
        timeout: float | None = None,
        check: bool = False,
        display_command: bool = True,
    ) -> CompletedProcess[str]:
        if display_command:
            display(cmd)
        try:
            return subprocess.run(
                cmd,
                stdout=stdout,
                stderr=stderr,
                capture_output=capture_output,
                text=True,
                timeout=timeout,
                check=check,
            )
        except FileNotFoundError as e:
            if e.filename == cmd[0]:
                raise CommandNotFoundError(e.filename) from e
            raise

    def popen(
        self,
        *cmd: str,
        stdout: int | IO[Any] | None = None,
        stderr: int | IO[Any] | None = None,
    ) -> subprocess.Popen:
        self.display(cmd)
        return subprocess.Popen(
            cmd, stdout=stdout, stderr=stderr, universal_newlines=True
        )

    def check_passwordless(self, host: str):
        return check_passwordless(host)


def display(split_command: list[str] | tuple[str, ...] | str) -> None:
    if isinstance(split_command, str):
        command = split_command
    else:
        command = shlex.join(split_command)
    print(T.bold_green("(local) $ ", command))


def check_passwordless(host: str) -> bool:
    if (
        sys.platform != "win32"
        and SSH_CONFIG_FILE.exists()
        and is_already_logged_in(host)
    ):
        return True

    try:
        connect_kwargs_for_host = {"allow_agent": False}
        if host in cluster_to_connect_kwargs:
            connect_kwargs_for_host.update(cluster_to_connect_kwargs[host])
        with fabric.Connection(
            host,
            connect_kwargs=connect_kwargs_for_host,
        ) as connection:
            results: fabric.runners.Result = connection.run(
                "echo OK",
                in_stream=False,
                echo=True,
                echo_format=T.bold_cyan(f"({host})" + " $ {command}"),
            )

    except (
        paramiko.ssh_exception.SSHException,
        paramiko.ssh_exception.NoValidConnectionsError,
        socket.gaierror,
        # BUG: Also getting ValueError("q must be exactlu 160, 224, or 256 bits long")
        # with older versions of paramiko.
        # ValueError,
    ) as err:
        logger.debug(f"Unable to connect to {host} without a password: {err}")
        return False

    if "OK" in results.stdout:
        return True
    logger.error("Unexpected output from SSH command, output didn't contain 'OK'!")
    logger.error(f"stdout: {results.stdout}, stderr: {results.stderr}")
    return False
