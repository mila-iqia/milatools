from __future__ import annotations

import subprocess
from abc import ABC, abstractmethod

from milatools.utils.remote_v1 import Hide


class Runner(ABC):
    """ABC for a Runner that runs commands on a (local or remote) machine."""

    hostname: str
    """Hostname of the machine that commands are ultimately being run on."""

    @abstractmethod
    def run(
        self,
        command: str,
        *,
        input: str | None = None,
        display: bool = True,
        warn: bool = False,
        hide: Hide = False,
    ) -> subprocess.CompletedProcess[str]:
        """Runs the given command on the remote and returns the result.

        This executes the command in an ssh subprocess, which, thanks to the
        ControlMaster/ControlPath/ControlPersist options, will reuse the existing
        connection to the remote.

        Parameters
        ----------
        command: The command to run.
        input: Input to pass to the program (argument to `subprocess.run`).
        display: Display the command on the console before it is run.
        warn: If `true` and an exception occurs, warn instead of raising the exception.
        hide: Controls the printing of the subprocess' stdout and stderr.

        Returns
        -------
        A `subprocess.CompletedProcess` object with the output of the subprocess.
        """
        # note: Could also have a default implementation just waits on the async method:
        # return asyncio.get_event_loop().run_until_complete(
        #     self.run_async(command, input=input, display=display, warn=warn, hide=hide)
        # )
        raise NotImplementedError()

    @abstractmethod
    async def run_async(
        self,
        command: str,
        *,
        input: str | None = None,
        display: bool = True,
        warn: bool = False,
        hide: Hide = False,
    ) -> subprocess.CompletedProcess[str]:
        """Runs the given command asynchronously and returns the result.

        This executes the command over ssh in an asyncio subprocess, which reuses the
        existing connection to the remote.

        Parameters
        ----------
        command: The command to run.
        input: Input to pass to the program (as if it was the 'input' argument to \
               `asyncio.subprocess.Process.communicate`).
        display: Display the command on the console before it is run.
        warn: If `true` and an exception occurs, warn instead of raising the exception.
        hide: Controls the printing of the subprocess' stdout and stderr.

        Returns
        -------
        A `subprocess.CompletedProcess` object with the output of the subprocess.
        """
        raise NotImplementedError()

    def get_output(
        self,
        command: str,
        *,
        display: bool = False,
        warn: bool = False,
        hide: Hide = True,
    ) -> str:
        """Runs the command and returns the stripped output string."""
        return self.run(command, display=display, warn=warn, hide=hide).stdout.strip()

    async def get_output_async(
        self,
        command: str,
        *,
        display: bool = False,
        warn: bool = False,
        hide: Hide = True,
    ) -> str:
        """Runs the command asynchronously and returns the stripped output string."""
        return (
            await self.run_async(command, display=display, warn=warn, hide=hide)
        ).stdout.strip()
