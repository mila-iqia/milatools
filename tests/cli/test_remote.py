"""Tests for the Remote and SlurmRemote classes."""
from __future__ import annotations

import shutil
import sys
import time
import typing
import unittest
import unittest.mock
from collections.abc import Callable, Generator, Iterable
from pathlib import Path
from unittest.mock import Mock

import invoke
import pytest
from fabric.connection import Connection
from pytest_regressions.file_regression import FileRegressionFixture

from milatools.cli.remote import (
    QueueIO,
    Remote,
    SlurmRemote,
    get_first_node_name,
)
from milatools.cli.utils import T, shjoin

from .common import function_call_string


@pytest.mark.parametrize("keepalive", [0, 123])
def test_init(
    keepalive: int,
    host: str,
    MockConnection: Mock,
    mock_connection: Mock,
):
    """This test shows the behaviour of __init__ to isolate it from other tests."""

    # This should have called the `Connection` class with the host, which we patched in
    # the fixture above.
    r = Remote(host, keepalive=keepalive)
    # The Remote should have created a Connection instance (which happens to be
    # the mock_connection we made above).
    MockConnection.assert_called_once_with(host, connect_kwargs={"banner_timeout": 60})
    assert r.connection is mock_connection

    # The connection's Transport is opened if a non-zero value is passed for `keepalive`
    if keepalive:
        assert len(mock_connection.method_calls) == 2
        mock_connection.open.assert_called_once()
        mock_connection.transport.set_keepalive.assert_called_once_with(keepalive)
    else:
        assert not mock_connection.method_calls
        mock_connection.open.assert_not_called()
        mock_connection.transport.set_keepalive.assert_not_called()


@pytest.mark.parametrize("keepalive", [0, 123])
def test_init_with_connection(
    keepalive: int,
    MockConnection: Mock,
    mock_connection: Mock,
):
    """This test shows the behaviour of __init__ to isolate it from other tests."""
    r = Remote(mock_connection.host, connection=mock_connection, keepalive=keepalive)
    MockConnection.assert_not_called()
    assert r.connection is mock_connection
    # The connection is not opened, and the transport is also not opened.
    assert not mock_connection.method_calls
    mock_connection.open.assert_not_called()
    mock_connection.transport.set_keepalive.assert_not_called()


# Note: We could actually run this for real also!
@pytest.mark.parametrize("command_to_run", ["echo OK"])
@pytest.mark.parametrize("initial_transforms", [[]])
@pytest.mark.parametrize(
    ("method", "args"),
    [
        (
            Remote.with_transforms,
            (
                lambda cmd: cmd.replace("OK", "NOT_OK"),
                lambda cmd: f"echo 'command before' && {cmd}",
            ),
        ),
        (
            Remote.wrap,
            ("echo 'echo wrap' && {}",),
        ),
        (
            Remote.with_precommand,
            ("echo 'echo precommand'",),
        ),
        # this need to be a file to source before running the command.
        (Remote.with_profile, (".bashrc",)),
        (Remote.with_bash, ()),
    ],
)
def test_remote_transform_methods(
    command_to_run: str,
    initial_transforms: list[Callable[[str], str]],
    method: Callable,
    args: tuple,
    host: str,
    mock_connection: Connection | Mock,
    file_regression: FileRegressionFixture,
    capsys: pytest.CaptureFixture,
):
    """Test the methods of `Remote` that modify the commands passed to `run` before it
    gets passed to the connection and run on the server."""
    mock_connection = mock_connection
    r = Remote(
        host,
        connection=mock_connection,
        transforms=initial_transforms,
    )
    # Call the method on the remote, which should return a new Remote.
    modified_remote: Remote = method(r, *args)
    assert modified_remote.hostname == r.hostname
    assert modified_remote.connection is r.connection

    result = modified_remote.run(command_to_run)

    out_err = capsys.readouterr()
    stdout = out_err.out
    stderr = out_err.err
    assert not stderr

    # TODO: Would also need to remove other color codes if there were any.
    v = T.bold_cyan("@")
    color_prefix, _, color_suffix = v.partition("@")
    stdout = stdout.replace(color_prefix, "").replace(color_suffix, "")

    assert len(mock_connection.method_calls) == 1
    mock_connection.run = typing.cast(Mock, mock_connection.run)
    mock_connection.run.assert_called_once()

    transformed_command = mock_connection.run.mock_calls[0][1][0]
    # "#Connection({mock_connection.host!r}),
    regression_file_text = f"""\
After creating a Remote like so:

```python
remote = {function_call_string(Remote, host, connection=mock_connection, transforms=())}
```

and then calling:

```python
transformed_remote = remote.{function_call_string(method, *args)}
result = transformed_remote.run({command_to_run!r})
```

Printed the following on the terminal:

```console
{stdout}
```

The command that eventually would be run on the cluster is:

```bash
{transformed_command}
```

and `result.stdout.strip()={repr(result.stdout.strip())}`.
"""
    file_regression.check(regression_file_text, extension=".md")


@pytest.mark.parametrize("message", ["foobar"])
def test_display(
    message: str,
    remote: Remote,
    capsys: pytest.CaptureFixture,
):
    remote.display(message)
    output = capsys.readouterr().out
    # NOTE: This way of testing is also resilient to Pytest's `-s` option being used,
    # since in that case some color codes are added to the output.
    assert output in (
        T.bold_cyan(f"({remote.hostname}) $ {message}") + "\n",
        f"({remote.hostname}) $ {message}\n",
    )


@pytest.fixture(params=[True, False])
def hide(
    request: pytest.FixtureRequest, capsys: pytest.CaptureFixture
) -> Generator[bool, None, None]:
    """If `hide=True` is passed to `run` nothing should be printed to stdout/stderr."""
    value: bool = request.param

    yield value
    if value:
        output = capsys.readouterr()
        assert output.out == ""
        assert output.err == ""


@pytest.mark.parametrize(("command", "expected_output"), [("echo OK", "OK")])
@pytest.mark.parametrize("asynchronous", [True, False])
@pytest.mark.parametrize("warn", [True, False])
@pytest.mark.parametrize("display", [True, False, None])
def test_run(
    remote: Remote,
    command: str,
    expected_output: str,
    asynchronous: bool,
    hide: bool,
    warn: bool,
    display: bool | None,
    capsys: pytest.CaptureFixture,
):
    output = remote.run(
        command, display=display, hide=hide, warn=warn, asynchronous=asynchronous
    )
    if asynchronous:
        import invoke.runners

        assert isinstance(output, invoke.runners.Promise)
        output = output.join()

    stdout, stderr = capsys.readouterr()

    # Check that the original command is displayed in STDOUT if `display` is True or
    # if display is unset and hide is False.
    if display is not None:
        assert (command in stdout) == display
    elif not hide:
        assert command in stdout
    else:
        assert command not in stdout
    assert not stderr  # shouldn't write anything to stderr.

    remote.connection.run.assert_called_once_with(
        command,
        asynchronous=asynchronous,
        hide=hide,
        warn=warn,
        out_stream=None,
        in_stream=False,
    )
    remote.connection.local.assert_not_called()

    assert output.stdout == expected_output + "\n"
    # NOTE: Not using a file regression fixture for now because of the large number
    # of files and the small number of tested commands.

    # file_regression.check(
    #     "\n".join(
    #         [
    #             f"With `remote = {repr(remote)}`:"
    #             f"calling `remote.{fn_call_string}` produced the following output:",
    #             "",
    #             "```console",
    #             stdout,
    #             "```",
    #             "",
    #         ]
    #     ),
    #     extension=".md",
    # )


@pytest.mark.parametrize("warn", [True, False])
def test_get_output(
    mock_connection: Mock,
    host: str,
    hide: bool,
    warn: bool,
):
    command = "echo OK"
    command_output = "OK"
    mock_result = Mock(wraps=invoke.runners.Result(Mock(wraps=command_output)))
    mock_connection.run.return_value = mock_result

    r = Remote(host, connection=mock_connection)
    output = r.get_output(command, display=None, hide=hide, warn=warn)
    assert output == command_output

    assert len(mock_connection.method_calls) == 1
    mock_connection.run.assert_called_once()

    assert mock_connection.run.mock_calls[0].args[0] == command
    mock_result.stdout.strip.assert_called_once_with()


@pytest.mark.parametrize("hide", [True, False])
@pytest.mark.parametrize("warn", [True, False])
def test_get_lines(
    mock_connection: Connection,
    host: str,
    hide: bool,
    warn: bool,
):
    """
    BUG: Seems like either a bug or the name is misleading! `get_lines`
    splits the output based on spaces, not lines!
    """
    expected_lines = ["Line 1 has this value", "Line 2 has this other value"]
    command = " && ".join(f"echo '{line}'" for line in expected_lines)
    command_output = "\n".join(expected_lines)
    mock_connection.run.return_value = invoke.runners.Result(stdout=command_output)
    r = Remote(host, connection=mock_connection)
    lines = r.get_lines(command, hide=hide, warn=warn)
    # NOTE: We'd expect this, but instead we get ['Line', '1', 'has', 'this', 'value',
    # TODO: Uncomment this if we fix `get_lines` to split based on lines, or remove this
    # comment if we remove/rename `get_lines` to get_output().split() or similar.
    # assert lines == expected_lines
    # This is what we currently get:
    assert (
        lines
        == r.get_output(command, hide=hide, warn=warn).split()
        == " ".join(expected_lines).split()
    )


def write_lines_with_sleeps(lines: Iterable[str], sleep_time: float = 0.1):
    for line in lines:
        print(line)
        time.sleep(sleep_time)


@pytest.mark.parametrize("wait", [True, False])
@pytest.mark.parametrize("pty", [True, False])
def test_extract(
    remote: Remote,
    wait: bool,
    pty: bool,
):
    name = "bob"
    lines = [
        "line 1",
        f"hello my name is {name}",
        "line 3",
        "done",
    ]
    patterns = {
        "foo": r"hello my name is ([A-Za-z0-9_-]+)",
    }
    expected_output = {
        "foo": name,
    }

    _runner, out = remote.extract(
        # """for i in {1..15} ; do echo "This is the ${i}th echo" ; sleep 5 ; done""",
        "&& sleep 0.2 && ".join(f"echo '{line}'" for line in lines),
        patterns=patterns,
        wait=wait,
        pty=pty,
        hide=False,
    )
    assert out == expected_output


def _xfail_if_not_on_localhost(host: str):
    if host != "localhost":
        pytest.xfail("This test only works on localhost.")


def test_get(remote: Remote, tmp_path: Path, host: str):
    # TODO: Make this test smarter? or no need? (because we'd be testing fabric at that
    # point?)
    _xfail_if_not_on_localhost(remote.hostname)
    src = tmp_path / "foo"
    dest = tmp_path / "bar"
    source_content = "hello hello"
    src.write_text(source_content)
    _result = remote.get(str(src), str(dest))
    remote.connection.get.assert_called_once_with(str(src), str(dest))
    assert dest.read_text() == source_content


def test_put(remote: Remote, tmp_path: Path):
    _xfail_if_not_on_localhost(remote.hostname)
    src = tmp_path / "foo"
    dest = tmp_path / "bar"
    source_content = "hello hello"
    src.write_text(source_content)

    result = remote.put(str(src), str(dest))
    remote.connection.put.assert_called_once_with(str(src), str(dest))
    import fabric.transfer

    assert isinstance(result, fabric.transfer.Result)
    assert dest.read_text() == source_content


def test_puttext(remote: Remote, tmp_path: Path):
    _xfail_if_not_on_localhost(remote.hostname)
    dest_dir = tmp_path / "bar/baz"
    dest = tmp_path / f"{dest_dir}/bob.txt"
    some_text = "foo"
    _result = remote.puttext(some_text, str(dest))
    remote.connection.run.assert_called_once()
    assert remote.connection.run.mock_calls[0].args[0] == f"mkdir -p {dest_dir}"
    # The first argument of `put` will be the name of a temporary file.
    remote.connection.put.assert_called_once_with(unittest.mock.ANY, str(dest))
    assert dest.read_text() == some_text


def test_home(remote: Remote):
    home_dir = remote.home()
    remote.connection.run.assert_called_once()
    assert remote.connection.run.mock_calls[0].args[0] == "echo $HOME"
    remote.connection.local.assert_not_called()
    if remote.hostname == "mila":
        assert home_dir.startswith("/home/mila/")
    elif remote.hostname == "localhost":
        assert home_dir == str(Path.home())


def test_persist(remote: Remote):
    assert remote.persist() is remote


def test_ensure_allocation(remote: Remote):
    assert remote.ensure_allocation() == ({"node_name": remote.hostname}, None)


def some_transform(x: str) -> str:
    return f"echo Hello && {x}"


def some_other_transform(x: str) -> str:
    return f"echo 'this is printed after the command' && {x}"


class TestSlurmRemote:
    @pytest.mark.parametrize("persist", [True, False])
    def test_init(self, mock_connection: Connection, persist: bool):
        alloc = ["--time=00:01:00"]
        transforms = [some_transform]
        remote = SlurmRemote(
            mock_connection, alloc=alloc, transforms=transforms, persist=persist
        )
        assert remote.connection is mock_connection
        assert remote._persist == persist
        assert remote.transforms == [
            *transforms,
            remote.srun_transform_persist if persist else remote.srun_transform,
        ]

    def test_srun_transform(self, mock_connection: Connection):
        alloc = ["--time=00:01:00"]
        transforms = [some_transform]
        persist: bool = False
        remote = SlurmRemote(
            mock_connection, alloc=alloc, transforms=transforms, persist=persist
        )
        command = "bob"
        assert remote.srun_transform(command) == f"srun {alloc[0]} bash -c {command}"

    def test_srun_transform_persist(
        self,
        mock_connection: Connection,
        host: str,
        file_regression: FileRegressionFixture,
        monkeypatch: pytest.MonkeyPatch,
    ):
        _xfail_if_not_on_localhost(host)
        alloc = ["--time=00:01:00"]
        remote = SlurmRemote(mock_connection, alloc=alloc, transforms=(), persist=False)
        command = "bob"

        # NOTE: It is unfortunately necessary for us to mock this function which we know
        # the `srun_transform_persist` method will call to get a temporary file name, so
        # that the regression file content is reproducible.
        mock_time_ns = Mock(return_value=1234567890)
        monkeypatch.setattr("time.time_ns", mock_time_ns)

        files_before = list((Path.home() / ".milatools" / "batch").rglob("*"))
        output_command = remote.srun_transform_persist(command)
        files_after = list((Path.home() / ".milatools" / "batch").rglob("*"))

        new_files = set(files_after) - set(files_before)

        assert len(new_files) == 1
        slurm_remote_constructor_call_str = function_call_string(
            SlurmRemote, mock_connection, alloc=alloc, transforms=(), persist=False
        )
        method_call_string = function_call_string(
            remote.srun_transform_persist, command
        )
        file_regression.check(
            "\n".join(
                [
                    "After creating a SlurmRemote like so:",
                    "",
                    "```python",
                    f"remote = {slurm_remote_constructor_call_str}",
                    "```",
                    "",
                    "Calling this:" "",
                    "```python",
                    f"remote.{method_call_string}",
                    "```",
                    "",
                    "created the following files (with abs path to the home directory "
                    "replaced with '$HOME' for tests):",
                    "\n".join(
                        "\n\n".join(
                            [
                                f"- {str(new_file).replace(str(Path.home()), '~')}:",
                                "",
                                "```",
                                new_file.read_text().replace(str(Path.home()), "$HOME"),
                                "```",
                            ]
                        )
                        for new_file in new_files
                    ),
                    "",
                    "and produced the following command as output (with the absolute "
                    "path to the home directory replaced with '$HOME' for tests):",
                    "",
                    "```bash",
                    output_command.replace(str(Path.home()), "$HOME"),
                    "```",
                    "",
                ]
            ),
            extension=".md",
        )
        # TODO: Need to create a fixture for `persist` that checks if any files were
        # created in ~/.milatools/batch, and if so, removes them after the test is done.
        # Remove any new files.
        for file in new_files:
            file.unlink()
        # If there wasn't a `~/.milatools` folder before, we should remove it after.
        if not files_before:
            shutil.rmtree(Path.home() / ".milatools")

    @pytest.mark.parametrize("persist", [True, False, None])
    def test_with_transforms(self, mock_connection: Connection, persist: bool | None):
        # NOTE: This just tests what the body of the `SlurmRemote.with_transforms` does.
        # It isn't a very useful test, but it's better than not having one for now.
        # The test for Remote.run above checks that `run` on the transformed remote
        # does what we expect.
        assert SlurmRemote.run is Remote.run
        alloc = ["--time=00:01:00"]
        transforms = [some_transform]
        new_transforms = [some_other_transform]
        original_persist: bool = False
        remote = SlurmRemote(
            mock_connection,
            alloc=alloc,
            transforms=transforms,
            persist=original_persist,
        )

        transformed = remote.with_transforms(*new_transforms, persist=persist)
        assert transformed.connection is remote.connection
        assert transformed.alloc == remote.alloc
        assert transformed.transforms == [
            some_transform,
            some_other_transform,
            (
                transformed.srun_transform_persist
                if persist
                else transformed.srun_transform
            ),
        ]
        assert transformed._persist == (remote._persist if persist is None else persist)

    @pytest.mark.parametrize("persist", [True, False])
    def test_persist(self, mock_connection: Connection, persist: bool):
        # NOTE: This just checks what the body of the `SlurmRemote.persist`
        # does. It isn't a very useful test, but it's better than not having one, for
        # now.
        alloc = ["--time=00:01:00"]
        transforms = [some_transform]
        remote = SlurmRemote(
            mock_connection, alloc=alloc, transforms=transforms, persist=persist
        )
        persisted = remote.persist()
        assert persisted.connection == remote.connection
        assert persisted.alloc == remote.alloc
        assert persisted.transforms == [
            some_transform,
            persisted.srun_transform_persist,
        ]
        assert persisted._persist is True

        persisted.run

    def test_ensure_allocation_persist(self, mock_connection: Connection):
        # TODO: This test is not smart. It basically replicates the content of the
        # method. We need to rework the SlurmRemote class so it is easier to test.
        alloc = ["--time=00:01:00"]
        remote = SlurmRemote(mock_connection, alloc=alloc, transforms=(), persist=True)
        node_job_info = {"node_name": "bob", "jobid": "1234"}
        remote.extract = Mock(
            spec=remote.extract,
            spec_set=True,
            return_value=(
                Mock(spec=invoke.runners.Runner, spec_set=True),
                node_job_info,
            ),
        )

        results, _runner = remote.ensure_allocation()

        remote.extract.assert_called_once_with(
            "echo @@@ $(hostname) @@@ && sleep 1000d",
            patterns={
                "node_name": "@@@ ([^ ]+) @@@",
                "jobid": "Submitted batch job ([0-9]+)",
            },
            hide=True,
        )
        assert results == node_job_info

    def test_ensure_allocation_without_persist(self, mock_connection: Connection):
        # TODO: This test is not smart. It basically replicates the content of the
        # method. We need to rework the SlurmRemote class so it is easier to test.
        alloc = ["--time=00:01:00"]
        remote = SlurmRemote(mock_connection, alloc=alloc, transforms=(), persist=False)
        node = "bob-123"
        expected_command = (
            f"cd $SCRATCH && salloc {shjoin(alloc)}"
            if mock_connection.host == "mila"
            else f"salloc {shjoin(alloc)}"
        )

        def write_stuff(
            command: str,
            asynchronous: bool,
            hide: bool,
            pty: bool,
            out_stream: QueueIO,
            warn: bool,
            in_stream: bool,
        ):
            assert command == expected_command
            out_stream.write(f"salloc: Nodes {node} are ready for job")
            return unittest.mock.DEFAULT

        mock_connection.run.side_effect = write_stuff
        results, _runner = remote.ensure_allocation()

        mock_connection.run.assert_called_once_with(
            expected_command,
            hide=False,
            warn=False,
            asynchronous=True,
            out_stream=unittest.mock.ANY,
            pty=True,
            in_stream=False,
        )
        assert results == {"node_name": node}


def test_QueueIO(file_regression: FileRegressionFixture):
    # TODO: This test doesn't do much.
    qio = QueueIO()
    strs = []

    i = 0

    qio.write("Begin")
    for _ in range(3):
        qio.write(f"\nline {i}")
        i += 1
    strs.append("".join(qio.readlines(lambda: True)))

    for _ in range(7):
        qio.write(f"\nline {i}")
        i += 1
    strs.append("".join(qio.readlines(lambda: True)))

    for _ in range(4):
        qio.write(f"\nline {i}")
        i += 1
    strs.append("".join(qio.readlines(lambda: True)))

    file_regression.check("\n=====".join(strs) + "\n^^^^^")


@pytest.mark.parametrize(
    ("node_string", "expected"),
    [
        ("cn-c001", "cn-c001"),
        ("cn-c[001-003]", "cn-c001"),
        ("cn-c[005,008]", "cn-c005"),
        ("cn-c001,rtx8", "cn-c001"),
    ],
)
def test_get_first_node_name(node_string: str, expected: str):
    assert get_first_node_name(node_string) == expected
