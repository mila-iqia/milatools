from __future__ import annotations

import subprocess
from subprocess import CompletedProcess
from typing import Callable, Iterable, Sequence

from typing_extensions import Concatenate, ParamSpec

from .utils import CommandNotFoundError, T, shjoin

P = ParamSpec("P")
Args = Concatenate[Sequence[str], P]


class Local:
    def display(self, args: Iterable[str]) -> None:
        print(T.bold_green("(local) $ ", shjoin(args)))

    def silent_get(
        self,
        cmd: Sequence[str],
        _check_output_fn: Callable[
            Concatenate[str | Sequence[str], P], str
        ] = subprocess.check_output,
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> str:
        kwargs["universal_newlines"] = True
        return _check_output_fn(cmd, *args, **kwargs)

    def get(
        self,
        cmd: Sequence[str],
        _check_output_fn: Callable[
            Concatenate[str | Sequence[str], P], str
        ] = subprocess.check_output,
        *args: P.args,
        **kwargs: P.kwargs,
    ):
        self.display(cmd)
        kwargs["universal_newlines"] = True
        return _check_output_fn(cmd, *args, **kwargs)

    def run(
        self,
        cmd: Sequence[str],
        _run_fn: Callable[Args[P], CompletedProcess[str]] = subprocess.run,
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> CompletedProcess[str]:
        self.display(cmd)
        try:
            kwargs["universal_newlines"] = True
            return _run_fn(cmd, *args, **kwargs)
        except FileNotFoundError as e:
            if e.filename == cmd[0]:
                raise CommandNotFoundError(e.filename)
            raise e

    def popen(
        self,
        cmd: Sequence[str],
        _popen_fn: Callable[Args[P], subprocess.Popen] = subprocess.Popen,
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> subprocess.Popen:
        self.display(cmd)
        kwargs["universal_newlines"] = True
        return _popen_fn(cmd, *args, **kwargs)

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
