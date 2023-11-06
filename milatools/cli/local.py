from __future__ import annotations

import subprocess
from subprocess import CompletedProcess
from typing import IO, Any, Iterable, Sequence

from .utils import CommandNotFoundError, T, shjoin


class Local:
    def display(self, args: Iterable[str]) -> None:
        print(T.bold_green("(local) $ ", shjoin(args)))

    def silent_get(self, cmd: Sequence[str]) -> str:
        return subprocess.check_output(cmd, universal_newlines=True)

    def get(self, cmd: Sequence[str]) -> str:
        self.display(cmd)
        return subprocess.check_output(cmd, universal_newlines=True)

    def run(
        self,
        cmd: Sequence[str],
        *,
        stdout: int | IO[Any] | None = None,
        stderr: int | IO[Any] | None = None,
        capture_output: bool = False,
    ) -> CompletedProcess[str]:
        self.display(cmd)
        try:
            return subprocess.run(
                cmd,
                stdout=stdout,
                stderr=stderr,
                capture_output=capture_output,
                universal_newlines=True,
            )
        except FileNotFoundError as e:
            if e.filename == cmd[0]:
                raise CommandNotFoundError(e.filename) from e
            raise

    def popen(
        self,
        cmd: Sequence[str],
        *,
        stdout: int | IO[Any] | None = None,
        stderr: int | IO[Any] | None = None,
    ) -> subprocess.Popen:
        self.display(cmd)
        return subprocess.Popen(
            cmd, stdout=stdout, stderr=stderr, universal_newlines=True
        )

    def check_passwordless(self, host: str) -> bool:
        results = self.run(
            [
                "ssh",
                "-oPreferredAuthentications=publickey",
                host,
                "echo OK",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if results.returncode != 0:
            if "Permission denied" in results.stderr:
                return False
            print(results.stdout)
            print(results.stderr)
            exit(f"Failed to connect to {host}, could not understand error")
        # TODO: Perhaps we could actually check the output of the command here!
        # elif "OK" in results.stdout:
        else:
            print("# OK")
            return True
