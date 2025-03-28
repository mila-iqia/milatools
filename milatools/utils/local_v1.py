from __future__ import annotations

import shlex
import subprocess
from logging import getLogger as get_logger
from subprocess import CompletedProcess
from typing import IO, Any

from typing_extensions import deprecated

from milatools.cli.utils import CommandNotFoundError, T

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


def display(split_command: list[str] | tuple[str, ...] | str) -> None:
    if isinstance(split_command, str):
        command = split_command
    else:
        command = shlex.join(split_command)
    print(T.bold_green("(local) $ ", command))
