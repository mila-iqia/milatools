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
        check: bool = False,
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

    def check_passwordless(self, host: str, timeout: int | None = None):
        try:
            results = self.run(
                "ssh",
                "-oPreferredAuthentications=publickey",
                "-oStrictHostKeyChecking=no",
                host,
                "echo OK",
                capture_output=True,
                check=True,
                timeout=timeout,
            )

        except subprocess.TimeoutExpired:
            logger.debug(
                f"Timeout ({timeout}s) while connecting to {host}, must be waiting "
                f"for a password."
            )
            return False
        except subprocess.CalledProcessError as err:
            logger.debug(
                f"Unable to connect to {host} without a password: {err.stderr}"
            )
            return False

        if "OK" in results.stdout:
            print("# OK")
            return True
        logger.error("Unexpected output from SSH command, output didn't contain 'OK'!")
        logger.error(f"stdout: {results.stdout}, stderr: {results.stderr}")
        return False
