from __future__ import annotations

import subprocess
from logging import getLogger as get_logger
from subprocess import CompletedProcess
from typing import IO, Any

from typing_extensions import deprecated

from .utils import CommandNotFoundError, T, shjoin

logger = get_logger(__name__)


class Local:
    def display(self, args: list[str] | tuple[str, ...]) -> None:
        print(T.bold_green("(local) $ ", shjoin(args)))

    def silent_get(self, *cmd: str) -> str:
        return subprocess.check_output(cmd, universal_newlines=True)

    @deprecated("This isn't used and will probably be removed. Don't start using it.")
    def get(self, *cmd: str) -> str:
        self.display(cmd)
        return subprocess.check_output(cmd, universal_newlines=True)

    def run(
        self,
        *cmd: str,
        stdout: int | IO[Any] | None = None,
        stderr: int | IO[Any] | None = None,
        capture_output: bool = False,
        timeout: float | None = None,
    ) -> CompletedProcess[str]:
        self.display(cmd)
        try:
            return subprocess.run(
                cmd,
                stdout=stdout,
                stderr=stderr,
                capture_output=capture_output,
                universal_newlines=True,
                timeout=timeout,
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

    def check_passwordless(self, host: str, timeout: int | None = None):
        try:
            results = self.run(
                "ssh",
                "-oPreferredAuthentications=publickey",
                "-oStrictHostKeyChecking=no",
                host,
                "echo OK",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as err:
            if err.stdout is None and err.stderr is None:
                logger.debug(
                    f"Timeout ({timeout}s) while connecting to {host}, must be waiting "
                    f"for a password."
                )
                return False
            logger.debug(
                f"Timeout while connecting to {host}, and got unexpected output:\n"
                f"stdout: {err.stdout}\n"
                f"stderr: {err.stderr}"
            )
            return False

        if results.returncode != 0:
            if "Permission denied" in results.stderr:
                return False
            else:
                print(results.stdout)
                print(results.stderr)
                exit(f"Failed to connect to {host}, could not understand error")
        else:
            print("# OK")
            return True
