from __future__ import annotations

import contextlib
import re
import sys
import time
import typing
import unittest
import unittest.mock
from typing import Callable, Generator, Iterable
from unittest.mock import Mock, create_autospec

import invoke
import pytest
from fabric.testing.fixtures import Connection
from pytest_regressions.file_regression import FileRegressionFixture
from typing_extensions import ParamSpec

from milatools.cli.remote import (
    QueueIO,
    Remote,
    SlurmRemote,
    get_first_node_name,
)
from milatools.cli.utils import T, shjoin
from tests.cli.common import function_call_string

from .conftest import internet_access

# TODO: Enable running the tests "for real" on the mila cluster using a flag?
# - This would require us to use "proper" commands e.g. 'echo OK' can't output "bobobo".
# RUN_COMMANDS_FOR_REAL = "--enable-internet" in sys.argv

disable_internet_access = pytest.mark.disable_socket
only_runs_for_real = internet_access("remote_only")
dont_run_for_real = internet_access("local_only")
can_run_for_real = internet_access("either")

P = ParamSpec("P")


requires_s_flag = pytest.mark.skipif(
    "-s" not in sys.argv,
    reason=(
        "Seems to require reading from stdin? Works with the -s flag, but other "
        "tests might not."
    ),
)


@pytest.fixture
def host() -> str:
    return "mila"


@pytest.fixture
def MockConnection(
    internet_enabled: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> type[Connection] | Mock:
    """Fixture that mocks the `fabric.Connection` class.

    If `internet_enabled` is True, then this doesn't mock the class.
    """
    if internet_enabled:
        return Connection

    MockConnection = create_autospec(
        Connection,
        spec_set=True,
        _name="MockConnection",
    )
    import milatools.cli.remote

    # NOTE: Doesn't seem to be necessary to mock paramiko.Transport at this point.
    monkeypatch.setattr(milatools.cli.remote, Connection.__name__, MockConnection)
    return MockConnection


@pytest.fixture
def mock_connection(
    host: str,
    MockConnection: type[Connection] | Mock,
    internet_enabled: bool,
) -> Connection | Mock:
    """Creates a mock `Connection` object if internet access is disabled."""

    if internet_enabled:
        return Connection(host=host)

    assert isinstance(MockConnection, Mock)
    mock_connection: Mock = MockConnection.return_value
    mock_connection.configure_mock(
        host=host,
        # Modify the repr so they show up nicely in the regression files and with
        # consistent/reproducible names.
        __repr__=lambda _: f"Connection({repr(host)})",
    )
    return mock_connection


@disable_internet_access
@pytest.mark.parametrize("keepalive", [0, 123])
@pytest.mark.parametrize("host", ["mila", "localhost"])
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
    MockConnection.assert_called_once_with(host)
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


@disable_internet_access
@pytest.mark.parametrize("keepalive", [0, 123])
@pytest.mark.parametrize("host", ["mila", "localhost"])
def test_init_with_connection(
    keepalive: int,
    host: str,
    MockConnection: Mock,
    mock_connection: Mock,
):
    """This test shows the behaviour of __init__ to isolate it from other tests."""
    r = Remote(host, connection=mock_connection, keepalive=keepalive)
    MockConnection.assert_not_called()
    assert r.connection is mock_connection
    # The connection is not opened, and the transport is also not opened.
    assert not mock_connection.method_calls
    mock_connection.open.assert_not_called()
    mock_connection.transport.set_keepalive.assert_not_called()


# Note: We could actually run this for real!
@pytest.mark.disable_socket
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
        (Remote.with_profile, ("profile",)),
        (Remote.with_bash, ()),
    ],
)
def test_remote_transform_methods(
    host: str,
    mock_connection: Connection | Mock,
    command_to_run: str,
    initial_transforms: list[Callable[[str], str]],
    method: Callable,
    args: tuple,
    file_regression: FileRegressionFixture,
    capsys: pytest.CaptureFixture,
):
    """Test the methods of `Remote` that modify the commands passed to `run` before it
    gets passed to the connection and run on the server."""
    connection = mock_connection
    r = Remote(
        host,
        connection=connection,
        transforms=initial_transforms,
    )
    # Call the method on the remote, which should return a new Remote.
    modified_remote: Remote = method(r, *args)
    assert modified_remote.hostname == r.hostname
    assert modified_remote.connection is r.connection

    with contextlib.redirect_stderr(sys.stdout):
        modified_remote.run(command_to_run)

    out_err = capsys.readouterr()
    stdout, stderr = out_err.out, out_err.err
    assert not stderr
    if "-s" in sys.argv:
        v = T.bold_cyan("@")
        color_prefix, _, color_suffix = v.partition("@")
        stdout = stdout.replace(color_prefix, "").replace(color_suffix, "")
        # stdout = remove_color_codes(stdout)

    assert len(mock_connection.method_calls) == 1
    mock_connection.run = typing.cast(Mock, mock_connection.run)
    mock_connection.run.assert_called_once()

    transformed_command = mock_connection.run.mock_calls[0][1][0]
    # "#Connection({mock_connection.host!r}),
    regression_file_text = f"""\
After creating a Remote like so:

```python
remote = {function_call_string(Remote, host, connection=connection, transforms=())}
```

and then calling:

```python
transformed_remote = remote.{function_call_string(method, *args)}
transformed_remote.run({command_to_run!r})
```

Printed the following on the terminal:

```console
{stdout}
```

The command that eventually would be run on the cluter is:

```bash
{transformed_command}
```
"""
    file_regression.check(regression_file_text, extension=".md")


@can_run_for_real
@pytest.mark.parametrize("message", ["foobar"])
def test_display(
    message: str,
    mock_connection: Connection | Mock,
    host: str,
    capsys: pytest.CaptureFixture,
):
    r = Remote(host, connection=mock_connection)
    r.display(message)
    output = capsys.readouterr().out
    # NOTE: This way of testing is also resilient to Pytest's `-s` option being used,
    # since in that case some color codes are added to the output.
    assert output in (
        T.bold_cyan(f"({host}) $ {message}") + "\n",
        # f"\x1b[1m\x1b[36m({host}) $ {message}\x1b(B\x1b[m\n",
        f"({host}) $ {message}\n",
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


@disable_internet_access
@pytest.mark.parametrize("asynchronous", [True, False])
@pytest.mark.parametrize("warn", [True, False])
@pytest.mark.parametrize("display", [True, False, None])
def test_run(
    mock_connection: Connection | Mock,
    host: str,
    asynchronous: bool,
    hide: bool,
    warn: bool,
    display: bool | None,
    capsys: pytest.CaptureFixture,
):
    command = "echo OK"
    command_output = "bob"
    r = Remote(host, connection=mock_connection)
    mock_promise = None
    if asynchronous:
        mock_promise = create_autospec(
            invoke.runners.Promise,
            name="mock_promise",
            spec_set=True,
            instance=True,
        )
        mock_promise.join.return_value = (
            Mock(wraps=invoke.runners.Result(stdout=command_output), spec_set=True),
        )
        mock_connection.run.return_value = mock_promise

    output = r.run(
        command, display=display, hide=hide, warn=warn, asynchronous=asynchronous
    )
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

    mock_connection.run.assert_called_once_with(
        command,
        asynchronous=asynchronous,
        hide=hide,
        warn=warn,
    )
    mock_connection.local.assert_not_called()
    if asynchronous:
        assert mock_promise
        assert output is mock_promise
        assert not mock_promise.method_calls  # Run doesn't call `join` on the promise.


@can_run_for_real
@pytest.mark.parametrize("warn", [True, False])
def test_get_output(
    mock_connection: Connection | Mock,
    host: str,
    hide: bool,
    warn: bool,
    internet_enabled: bool,
):
    command = "echo OK"
    r = Remote(host, connection=mock_connection)
    if internet_enabled:
        output = r.get_output(command, display=None, hide=hide, warn=warn)
        assert output == "OK"
    else:
        command_output = "bob"
        mock_connection.run.return_value = invoke.runners.Result(stdout=command_output)
        output = r.get_output(command, display=None, hide=hide, warn=warn)
        mock_connection.run.assert_called_once_with(command, hide=hide, warn=warn)
        assert output == command_output


@dont_run_for_real
@pytest.mark.parametrize("hide", [True, False])
@pytest.mark.parametrize("warn", [True, False])
def test_get_lines(
    mock_connection: Connection,
    host: str,
    hide: bool,
    warn: bool,
    internet_enabled: bool,
):
    """
    BUG: Seems like either a bug or the name is misleading! `get_lines`
    splits the output based on spaces, not lines!
    """
    expected_lines = ["Line 1 has this value", "Line 2 has this other value"]
    command = " && ".join(f"echo '{line}'" for line in expected_lines)
    command_output = "\n".join(expected_lines)
    if not internet_enabled:
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


@dont_run_for_real
@disable_internet_access
@pytest.mark.parametrize("wait", [True, False])
@pytest.mark.parametrize("pty", [True, False])
@pytest.mark.parametrize("hide", [True, False])
def test_extract(
    mock_connection: Connection,
    host: str,
    wait: bool,
    pty: bool,
    hide: bool,
):
    """TODO: Rewrite this to use `write_line_with_sleeps` above or similar."""

    test_command = "echo 'hello my name is $USER'"
    command_output = "hello my name is bob"
    pattern = r"hello my name is ([A-Za-z0-9_-]+)"
    expected_out = "bob"

    mock_runner: Mock = create_autospec(
        invoke.runners.Runner,
        spec_set=True,
        instance=True,
        _name="mock_runner",
    )

    # NOTE: The runner needs to write stuff to into the out_stream. This is a bit
    # tricky.
    write_stuff_was_called = False

    def write_stuff(
        command: str,
        asynchronous: bool,
        hide: bool,
        pty: bool,
        out_stream: QueueIO,
    ):
        nonlocal write_stuff_was_called
        assert command == test_command
        write_stuff_was_called = True
        out_stream.write(command_output)
        return unittest.mock.DEFAULT
        # return invoke.runners.Promise(mock_runner)

    mock_runner.run.side_effect = write_stuff
    mock_runner.process_is_finished = Mock(spec=bool, side_effect=[False, True])
    # mock_connection.
    mock_connection._remote_runner.return_value = mock_runner

    mock_promise = create_autospec(
        invoke.runners.Promise,
        _name="mock_promise",
        # spec_set=True,
        instance=True,
        runner=mock_runner,
    )
    mock_connection.run.return_value = mock_promise

    # TODO: This makes the test pass, but it becomes pretty meaningless at this point.
    # Both the Promise/Result, Runner, etc don't get used. I'm (@lebrice) not sure
    mock_connection.run.side_effect = write_stuff
    r = Remote(hostname=host, connection=mock_connection)

    match = re.match(pattern, command_output)
    assert match and match.groups()[0] == "bob"

    some_key = "foo"
    runner, out = r.extract(
        test_command,
        patterns={
            some_key: pattern,
        },
        wait=wait,
        pty=pty,
        hide=hide,
    )
    assert write_stuff_was_called
    assert out == {some_key: expected_out}


@dont_run_for_real
@disable_internet_access
def test_get(mock_connection: Connection, host: str):
    # TODO: Make this test smarter? or no need? (because we'd be testing fabric at that
    # point?)
    r = Remote(host, connection=mock_connection)
    _result = r.get("foo", "bar")
    mock_connection.get.assert_called_once_with("foo", "bar")


@dont_run_for_real
@disable_internet_access
def test_put(mock_connection: Connection, host: str):
    r = Remote(host, connection=mock_connection)
    _result = r.put("foo", "bar")
    mock_connection.put.assert_called_once_with("foo", "bar")


@dont_run_for_real
@disable_internet_access
def test_puttext(mock_connection: Connection, host: str):
    r = Remote(host, connection=mock_connection)
    dest_dir = "bar/baz"
    dest = f"{dest_dir}/bob.txt"
    _result = r.puttext("foo", dest)
    mock_connection.run.assert_called_once_with(
        f"mkdir -p {dest_dir}",
        hide=True,
    )
    mock_connection.put.assert_called_once_with(unittest.mock.ANY, dest)


@can_run_for_real
def test_home(mock_connection: Connection, host: str, internet_enabled: bool):
    r = Remote(host, connection=mock_connection)
    home_dir = r.home()
    if internet_enabled:
        assert home_dir.startswith("/home/mila/")
    else:
        mock_connection.run.assert_called_once_with("echo $HOME", hide=True)
        mock_connection.local.assert_not_called()


@pytest.mark.skipif(
    "-s" not in sys.argv, reason="TODO: Seems to require the -s option?!"
)
@dont_run_for_real
@disable_internet_access
def test_persist(mock_connection: Connection, host: str, capsys: pytest.CaptureFixture):
    r = Remote(host, connection=mock_connection)
    r.persist()
    assert (
        "Warning: --persist does not work with --node or --job"
        in capsys.readouterr().out
    )


@pytest.fixture
def remote(mock_connection: Connection, host: str) -> Remote:
    return Remote(hostname=host, connection=mock_connection)


@dont_run_for_real
@disable_internet_access
def test_ensure_allocation(remote: Remote):
    assert remote.ensure_allocation() == ({"node_name": remote.hostname}, None)


def some_transform(x: str) -> str:
    return f"echo Hello && {x}"


def some_other_transform(x: str) -> str:
    return f"echo 'this is printed after the command' && {x}"


class TestSlurmRemote:
    @dont_run_for_real
    @pytest.mark.parametrize("persist", [True, False])
    def test_init_(self, mock_connection: Connection, persist: bool):
        alloc = ["--time=00:01:00"]
        transforms = [some_transform]
        remote = SlurmRemote(
            mock_connection, alloc=alloc, transforms=transforms, persist=persist
        )
        # TODO: This kind of test feels a bit dumb.
        assert remote.connection is mock_connection
        assert remote._persist == persist
        assert remote.transforms == [
            *transforms,
            remote.srun_transform_persist if persist else remote.srun_transform,
        ]

    @can_run_for_real
    def test_srun_transform(self, mock_connection: Connection):
        alloc = ["--time=00:01:00"]
        transforms = [some_transform]
        persist: bool = False
        remote = SlurmRemote(
            mock_connection, alloc=alloc, transforms=transforms, persist=persist
        )
        command = "bob"
        assert remote.srun_transform(command) == f"srun {alloc[0]} bash -c {command}"

    @dont_run_for_real
    @pytest.mark.skip(reason="Seems a bit hard to test for what it's worth..")
    def test_srun_transform_persist(self, mock_connection: Connection):
        alloc = ["--time=00:01:00"]
        transforms = [some_transform]
        persist: bool = False
        remote = SlurmRemote(
            mock_connection, alloc=alloc, transforms=transforms, persist=persist
        )
        output_file = "<some_file>"
        assert (
            remote.srun_transform_persist("bob")
            == f"bob; touch {output_file}; tail -n +1 -f {output_file}"
        )

    @dont_run_for_real
    @pytest.mark.parametrize("persist", [True, False, None])
    def test_with_transforms(self, mock_connection: Connection, persist: bool | None):
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

    @dont_run_for_real
    @pytest.mark.parametrize("persist", [True, False])
    def test_persist(self, mock_connection: Connection, persist: bool):
        alloc = ["--time=00:01:00"]
        transforms = [some_transform]
        remote = SlurmRemote(
            mock_connection, alloc=alloc, transforms=transforms, persist=persist
        )
        persisted = remote.persist()

        # NOTE: Feels dumb to do this. Not sure what I should be doing otherwise.
        assert persisted.connection == remote.connection
        assert persisted.alloc == remote.alloc
        assert persisted.transforms == [
            some_transform,
            persisted.srun_transform_persist,
        ]
        assert persisted._persist is True

    @dont_run_for_real
    @disable_internet_access
    def test_ensure_allocation_persist(self, mock_connection: Connection):
        alloc = ["--time=00:01:00"]
        transforms = [some_transform]
        remote = SlurmRemote(
            mock_connection, alloc=alloc, transforms=transforms, persist=True
        )
        node_job_info = {"node_name": "bob", "jobid": "1234"}
        # TODO: Not sure if this test has any use at this point..
        remote.extract = Mock(
            spec=remote.extract,
            spec_set=True,
            return_value=(
                Mock(spec=invoke.runners.Runner, spec_set=True),
                node_job_info,
            ),
        )

        results, runner = remote.ensure_allocation()

        remote.extract.assert_called_once_with(
            "echo @@@ $(hostname) @@@ && sleep 1000d",
            patterns={
                "node_name": "@@@ ([^ ]+) @@@",
                "jobid": "Submitted batch job ([0-9]+)",
            },
            hide=True,
        )
        assert results == node_job_info

    @dont_run_for_real
    @disable_internet_access
    def test_ensure_allocation_without_persist(self, mock_connection: Connection):
        alloc = ["--time=00:01:00"]
        transforms = [some_transform]
        remote = SlurmRemote(
            mock_connection, alloc=alloc, transforms=transforms, persist=False
        )
        node = "bob-123"

        def write_stuff(
            command: str,
            asynchronous: bool,
            hide: bool,
            pty: bool,
            out_stream: QueueIO,
        ):
            assert command == f"bash -c 'salloc {shjoin(alloc)}'"
            out_stream.write(f"salloc: Nodes {node} are ready for job")
            return unittest.mock.DEFAULT

        mock_connection.run.side_effect = write_stuff
        results, runner = remote.ensure_allocation()

        mock_connection.run.assert_called_once_with(
            f"bash -c 'salloc {shjoin(alloc)}'",
            hide=False,
            asynchronous=True,
            out_stream=unittest.mock.ANY,
            pty=True,
        )
        assert results == {"node_name": node}


def test_QueueIO(file_regression: FileRegressionFixture):
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


def test_get_first_node_name(file_regression: FileRegressionFixture):
    file_regression.check(
        "\n".join(
            (
                get_first_node_name("cn-c001"),
                get_first_node_name("cn-c[001-003]"),
                get_first_node_name("cn-c[005,008]"),
                get_first_node_name("cn-c001,rtx8"),
            )
        )
    )
