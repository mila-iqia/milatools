from __future__ import annotations

import asyncio
import dataclasses
import shlex
import subprocess
import sys
from logging import getLogger as get_logger
from subprocess import CompletedProcess

from milatools.cli import console
from milatools.utils.remote_v1 import Hide
from milatools.utils.runner import Runner

logger = get_logger(__name__)


@dataclasses.dataclass(init=False, frozen=True)
class LocalV2(Runner):
    """A runner that runs commands in subprocesses on the local machine."""

    hostname = "localhost"

    @staticmethod
    def run(
        command: str | tuple[str, ...],
        input: str | None = None,
        display: bool = True,
        warn: bool = False,
        hide: Hide = False,
    ) -> CompletedProcess[str]:
        program_and_args = _display_command(command, input=input, display=display)
        return run(program_and_args=program_and_args, input=input, warn=warn, hide=hide)

    @staticmethod
    def get_output(
        command: str | tuple[str, ...],
        *,
        display: bool = False,
        warn: bool = False,
        hide: Hide = True,
    ) -> str:
        return LocalV2.run(
            command, display=display, warn=warn, hide=hide
        ).stdout.strip()

    @staticmethod
    async def run_async(
        command: str | tuple[str, ...],
        input: str | None = None,
        display: bool = True,
        warn: bool = False,
        hide: Hide = False,
    ) -> CompletedProcess[str]:
        program_and_args = _display_command(command, input=input, display=display)
        return await run_async(program_and_args, input=input, warn=warn, hide=hide)

    @staticmethod
    async def get_output_async(
        command: str | tuple[str, ...],
        *,
        display: bool = False,
        warn: bool = False,
        hide: Hide = True,
    ) -> str:
        """Runs the command asynchronously and returns the stripped output string."""
        return (
            await LocalV2.run_async(command, display=display, warn=warn, hide=hide)
        ).stdout.strip()


def _display_command(
    command: str | tuple[str, ...], input: str | None, display: bool
) -> tuple[str, ...]:
    """Converts the command to a tuple of strings if needed with `shlex.split` and
    optionally logs it to the console.

    Also shows the input that would be passed to the command, if any.
    """
    if isinstance(command, str):
        program_and_args = tuple(shlex.split(command))
        displayed_command = command
    else:
        program_and_args = command
        displayed_command = shlex.join(command)
    if display:
        if not input:
            console.log(
                f"(localhost) $ {displayed_command}",
                style="green",
                _stack_offset=2,
            )
        else:
            console.log(
                f"(localhost) $ {displayed_command}\n{input}",
                style="green",
                _stack_offset=2,
            )
    return program_and_args


def run(
    program_and_args: tuple[str, ...],
    input: str | None = None,
    warn: bool = False,
    hide: Hide = False,
) -> subprocess.CompletedProcess[str]:
    """Runs the command *synchronously* in a subprocess and returns the result.

    Parameters
    ----------
    program_and_args: The program and arguments to pass to it. This is a tuple of \
        strings, same as in `subprocess.Popen`.
    input: The optional 'input' argument to `subprocess.Popen.communicate()`.
    warn: When `True` and an exception occurs, warn instead of raising the exception.
    hide: Controls the printing of the subprocess' stdout and stderr.

    Returns
    -------
    The `subprocess.CompletedProcess` object with the result of the subprocess.

    Raises
    ------
    subprocess.CalledProcessError
        If an error occurs when running the command and `warn` is `False`.
    """
    displayed_command = shlex.join(program_and_args)
    if not input:
        logger.debug(f"Calling `subprocess.run` with {program_and_args=}")
    else:
        logger.debug(f"Calling `subprocess.run` with {program_and_args=} and {input=}")
    result = subprocess.run(
        program_and_args,
        shell=False,
        capture_output=True,
        text=True,
        check=not warn,
        input=input,
    )
    assert result.returncode is not None
    if warn and result.returncode != 0:
        message = (
            f"Command {displayed_command!r}"
            + (f" with {input=!r}" if input else "")
            + f" exited with {result.returncode}: {result.stderr=}"
        )
        logger.debug(message)
        if hide is not True:  # don't warn if hide is True.
            logger.warning(RuntimeWarning(message), stacklevel=2)

    if result.stdout:
        if hide not in [True, "out", "stdout"]:
            print(result.stdout)
        logger.debug(f"{result.stdout=}")
    if result.stderr:
        if hide not in [True, "err", "stderr"]:
            print(result.stderr, file=sys.stderr)
        logger.debug(f"{result.stderr=}")
    return result


async def run_async(
    program_and_args: tuple[str, ...],
    input: str | None = None,
    warn: bool = False,
    hide: Hide = False,
) -> subprocess.CompletedProcess[str]:
    """Runs the command *asynchronously* in a subprocess and returns the result.

    Parameters
    ----------
    program_and_args: The program and arguments to pass to it. This is a tuple of \
        strings, same as in `subprocess.Popen`.
    input: The optional 'input' argument to `subprocess.Popen.communicate()`.
    warn: When `True` and an exception occurs, warn instead of raising the exception.
    hide: Controls the printing of the subprocess' stdout and stderr.

    Returns
    -------
    A `subprocess.CompletedProcess` object with the result of the asyncio.Process.

    Raises
    ------
    subprocess.CalledProcessError
        If an error occurs when running the command and `warn` is `False`.
    """

    logger.debug(f"Calling `asyncio.create_subprocess_exec` with {program_and_args=}")
    proc = await asyncio.create_subprocess_exec(
        *program_and_args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.PIPE,
        shell=False,
    )
    if input:
        logger.debug(f"Sending {input=!r} to the subprocess' stdin.")
    stdout, stderr = await proc.communicate(input.encode() if input else None)

    assert proc.returncode is not None
    if proc.returncode != 0:
        message = (
            f"{program_and_args!r}"
            + (f" with input {input!r}" if input else "")
            + f" exited with {proc.returncode}"
            + (f": {stderr}" if stderr else "")
        )
        logger.debug(message)
        if not warn:
            if stderr:
                logger.error(stderr)
            raise subprocess.CalledProcessError(
                returncode=proc.returncode,
                cmd=program_and_args,
                output=stdout,
                stderr=stderr,
            )
        if hide is not True:  # don't warn if hide is True.
            logger.warning(RuntimeWarning(message))
    result = subprocess.CompletedProcess(
        args=program_and_args,
        returncode=proc.returncode,
        stdout=stdout.decode(),
        stderr=stderr.decode(),
    )
    if result.stdout:
        if hide not in [True, "out", "stdout"]:
            print(result.stdout)
        logger.debug(f"{result.stdout}")
    if result.stderr:
        if hide not in [True, "err", "stderr"]:
            print(result.stderr, file=sys.stderr)
        logger.debug(f"{result.stderr}")
    return result
