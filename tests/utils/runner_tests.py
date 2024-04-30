from __future__ import annotations

import abc
import asyncio
import logging
import re
import subprocess
import time
from unittest.mock import AsyncMock, Mock

import pytest

from milatools.utils.remote_v1 import Hide
from milatools.utils.remote_v2 import RemoteV2
from milatools.utils.runner import Runner


class RunnerTests(abc.ABC):
    """Tests for a `Runner` implementation.

    Subclasses have to implement these methods:
    - the `runner` fixture which should ideally be class or session-scoped;
    - the `command_with_result` fixture should return a tuple containing 3 items:
        - The command to run successfully
        - the expected stdout or a `re.Pattern` to match against the stdout
        - the expected stderr or a `re.Pattern` to match against the stderr
    - the `command_with_exception_and_stderr` fixture should return a tuple containing 3 items:
        (The command to run uncessfully, the expected exception, the expected stderr).
    """

    @abc.abstractmethod
    @pytest.fixture(scope="class")
    def runner(self) -> Runner:
        raise NotImplementedError()

    @pytest.fixture(
        scope="class",
        params=[
            ("echo OK", "OK", ""),
        ],
    )
    def command_with_result(self, request: pytest.FixtureRequest):
        """Parametrized fixture for commands that are expected to raise an exception.

        These should be a tuple of:
        - the command to run
        - the expected stdout or a regular expression that matches stdout;
        - the expected stderr or a regular expression that matches stderr

        Subclasses should override this fixture to provide more commands to run.
        """
        return request.param

    # @abc.abstractmethod
    @pytest.fixture(
        scope="class",
        params=[
            (
                "cat /does/not/exist",
                subprocess.CalledProcessError,
                re.compile(r"cat: /does/not/exist: No such file or directory"),
            ),
        ],
    )
    def command_with_exception_and_stderr(self, request: pytest.FixtureRequest):
        """Parametrized fixture for commands that are expected to raise an exception.

        These should be a tuple of:
        - the command to run
        - The type of exception that is expected to be raised
        - a string or regular expression that matches stderr.

        Subclasses should override this fixture to provide more commands to run.
        """
        return request.param

    @pytest.mark.parametrize("use_async", [False, True], ids=["sync", "async"])
    @pytest.mark.parametrize("display", [True, False])
    @pytest.mark.parametrize("hide", [True, False, "out", "err", "stdout", "stderr"])
    @pytest.mark.asyncio
    async def test_run(
        self,
        runner: Runner,
        command_with_result: tuple[str, str | re.Pattern, str | re.Pattern],
        hide: Hide,
        display: bool,
        capsys: pytest.CaptureFixture,
        caplog: pytest.LogCaptureFixture,
        use_async: bool,
    ):
        command, expected_output, expected_err = command_with_result

        if use_async:
            result = await runner.run_async(command, display=display, hide=hide)
        else:
            result = runner.run(command, display=display, hide=hide)

        self._shared_run_checks(
            runner=runner,
            hide=hide,
            display=display,
            capsys=capsys,
            caplog=caplog,
            command=command,
            expected_output=expected_output,
            expected_err=expected_err,
            result=result,
            warn=False,
        )

    @pytest.mark.parametrize("use_async", [False, True], ids=["sync", "async"])
    @pytest.mark.parametrize("display", [True, False])
    @pytest.mark.parametrize("hide", [True, False, "out", "err", "stdout", "stderr"])
    @pytest.mark.asyncio
    async def test_run_with_error(
        self,
        runner: Runner,
        command_with_exception_and_stderr: tuple[
            str, type[Exception], str | re.Pattern
        ],
        hide: Hide,
        display: bool,
        use_async: bool,
    ):
        command, expected_exception, expected_err = command_with_exception_and_stderr

        assert isinstance(expected_exception, type) and issubclass(
            expected_exception, Exception
        )
        # Should raise an exception of this type.
        with pytest.raises(expected_exception=expected_exception):
            if use_async:
                _ = await runner.run_async(command, display=display, hide=hide)
            else:
                _ = runner.run(command, display=display, hide=hide)

    @pytest.mark.parametrize("use_async", [False, True], ids=["sync", "async"])
    @pytest.mark.parametrize("display", [True, False])
    @pytest.mark.parametrize("hide", [True, False, "out", "err", "stdout", "stderr"])
    @pytest.mark.asyncio
    async def test_run_with_error_warn(
        self,
        runner: Runner,
        command_with_exception_and_stderr: tuple[
            str, type[Exception], str | re.Pattern
        ],
        hide: Hide,
        display: bool,
        capsys: pytest.CaptureFixture,
        caplog: pytest.LogCaptureFixture,
        use_async: bool,
    ):
        command, expected_exception, expected_err = command_with_exception_and_stderr

        assert isinstance(expected_exception, type) and issubclass(
            expected_exception, Exception
        )
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="milatools"):
            if use_async:
                result = await runner.run_async(
                    command, display=display, hide=hide, warn=True
                )
            else:
                result = runner.run(command, display=display, hide=hide, warn=True)

        assert result.stdout == ""
        self._shared_run_checks(
            runner=runner,
            hide=hide,
            display=display,
            capsys=capsys,
            caplog=caplog,
            command=command,
            expected_output="",
            expected_err=expected_err,
            result=result,
            warn=True,
        )

    def _shared_run_checks(
        self,
        runner: Runner,
        hide: Hide,
        display: bool,
        capsys: pytest.CaptureFixture,
        caplog: pytest.LogCaptureFixture,
        command: str,
        expected_output: str | re.Pattern,
        expected_err: str | re.Pattern,
        result: subprocess.CompletedProcess,
        warn: bool,
    ):
        self._check_result(expected_output, expected_err, result)
        self._check_printed_stdout_stderr(
            runner=runner,
            command=command,
            display=display,
            hide=hide,
            result=result,
            capsys=capsys,
        )
        self._check_warning_logs(
            hide=hide,
            caplog=caplog,
            command=command,
            expected_err=expected_err,
            warn=warn,
        )

    def _check_printed_stdout_stderr(
        self,
        runner: Runner,
        command: str,
        display: bool,
        hide: Hide,
        result: subprocess.CompletedProcess,
        capsys: pytest.CaptureFixture,
    ):
        printed_output, printed_err = capsys.readouterr()
        assert isinstance(printed_output, str)
        assert isinstance(printed_err, str)

        assert (f"({runner.hostname}) $ {command}" in printed_output) == display

        if result.stdout:
            stdout_should_be_printed = hide not in [
                True,
                "out",
                "stdout",
            ]
            stdout_was_printed = result.stdout in printed_output
            assert stdout_was_printed == stdout_should_be_printed

        if result.stderr:
            error_should_be_printed = hide not in [
                True,
                "err",
                "stderr",
            ]
            error_was_printed = result.stderr in printed_err
            assert error_was_printed == error_should_be_printed, (
                result.stderr,
                printed_err,
            )

    def _check_result(
        self,
        expected_output: str | re.Pattern,
        expected_err: str | re.Pattern,
        result: subprocess.CompletedProcess,
    ):
        if isinstance(expected_output, re.Pattern):
            assert expected_output.search(result.stdout)
        else:
            assert result.stdout.strip() == expected_output

        if isinstance(expected_err, re.Pattern):
            assert expected_err.search(result.stderr)
        else:
            assert result.stderr.strip() == expected_err

    def _check_warning_logs(
        self,
        command: str,
        warn: bool,
        hide: Hide,
        expected_err: str | re.Pattern,
        caplog: pytest.LogCaptureFixture,
    ):
        if not warn:
            # No warnings should have been logged.
            assert not any(
                record.levelname in ("WARNING", "ERROR", "CRITICAL")
                for record in caplog.records
            )
            return

        if hide is True:
            # Warnings not logged at all (because `warn=True` and `hide=True`).
            assert caplog.records == []
        elif isinstance(expected_err, str):
            assert len(caplog.records) == 1
            assert (
                caplog.records[0].message.strip()
                == f"Command {command!r} returned non-zero exit code 1: {expected_err}"
            )
        elif isinstance(expected_err, re.Pattern):
            assert len(caplog.records) == 1
            message = caplog.records[0].message.strip()
            assert expected_err.search(message)

    @pytest.mark.parametrize("use_async", [False, True], ids=["sync", "async"])
    @pytest.mark.asyncio
    async def test_get_output_calls_run(
        self,
        runner: Runner,
        use_async: bool,
        monkeypatch: pytest.MonkeyPatch,
    ):
        mock = Mock(spec=subprocess.CompletedProcess, stdout=Mock())
        command = "echo OK"
        if use_async:
            if (
                runner.run_async is type(runner).run_async
                and runner.get_output_async is type(runner).get_output_async
            ):
                # It's a static method! Path the class instead of the "instance".
                monkeypatch.setattr(
                    type(runner),
                    type(runner).run_async.__name__,
                    AsyncMock(spec=runner.run_async, spec_set=True, return_value=mock),
                )
            else:
                monkeypatch.setattr(
                    runner,
                    runner.run_async.__name__,
                    AsyncMock(spec=runner.run_async, spec_set=True, return_value=mock),
                )
            output = await runner.get_output_async(command)
        else:
            if (
                runner.run is type(runner).run
                and runner.get_output is type(runner).get_output
            ):
                # It's a static method! Path the class instead.
                monkeypatch.setattr(
                    type(runner),
                    type(runner).run.__name__,
                    Mock(spec=runner.run, spec_set=True, return_value=mock),
                )
            else:
                # It's a regular method:
                monkeypatch.setattr(
                    runner,
                    runner.run.__name__,
                    Mock(spec=runner.run, spec_set=True, return_value=mock),
                )
            output = runner.get_output(command)
        assert isinstance(output, Mock)
        assert output is mock.stdout.strip()

    @pytest.mark.asyncio
    async def test_run_async_runs_in_parallel(self, runner: RemoteV2):
        commands = [f"sleep {i}" for i in range(1, 3)]
        start_time = time.time()
        # Sequential time:
        sequential_results = [runner.get_output(command) for command in commands]
        sequential_time = time.time() - start_time

        start_time = time.time()
        parallel_results = await asyncio.gather(
            *(runner.get_output_async(command) for command in commands),
            return_exceptions=False,
        )
        parallel_time = time.time() - start_time

        assert sequential_results == parallel_results
        assert parallel_time < sequential_time
