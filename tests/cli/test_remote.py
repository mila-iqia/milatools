from __future__ import annotations

import re
import shlex
import sys
import typing
import unittest
import unittest.mock
from typing import Callable, Generator
from unittest.mock import Mock, create_autospec

import invoke
import paramiko
import pytest
import pytest_mock
from fabric.testing.base import Command, MockChannel, Session
from fabric.testing.fixtures import Connection, MockRemote  # client,
from pytest_regressions.file_regression import FileRegressionFixture
from typing_extensions import ParamSpec

from milatools.cli.remote import (
    QueueIO,
    Remote,
    SlurmRemote,
    get_first_node_name,
)

# TODO: Enable running the tests "for real" on the mila cluster using a flag?
# - This would require us to use "proper" commands e.g. 'echo OK' can't output "bobobo".
RUN_COMMANDS_FOR_REAL = "--enable-internet" in sys.argv

only_runs_for_real = pytest.mark.skipif(
    not RUN_COMMANDS_FOR_REAL,
    reason="Test needs to have real internet access to the cluster.",
)
disable_internet_access = pytest.mark.disable_socket
dont_run_for_real = pytest.mark.skipif(
    RUN_COMMANDS_FOR_REAL,
    reason="Test shouldn't run with real internet access to the cluster.",
)
can_run_for_real = (
    pytest.mark.enable_socket if RUN_COMMANDS_FOR_REAL else pytest.mark.disable_socket
)
pytestmark = pytest.mark.disable_socket

P = ParamSpec("P")


if typing.TYPE_CHECKING:
    # Note: Tryign to use this so that <Mock instance>.foo.baz.bob is also shown as of
    # type Mock. Doesn't currently work though.

    # class _Mock(unittest.mock.Mock):
    #     def __getattr__(self, name: str) -> _Mock:
    #         ...

    #     def __getattribute__(self, name: str) -> _Mock:
    #         ...

    class _MockRemote(MockRemote):
        # NOTE: This class isn't actually used. It's just here so we can see what
        # the signature of the test methods are.
        def expect(
            self,
            _session_fn: Callable[P, Session] = Session,
            *session_args: P.args,
            **session_kwargs: P.kwargs,
        ) -> MockChannel:
            # NOTE: Check this method for the main stuff:
            import fabric.testing.base

            fabric.testing.base.Session.generate_mocks
            return super().expect(*session_args, **session_kwargs)


# from fabric.testing.fixtures import remote  # noqa


@disable_internet_access
@pytest.mark.skip(
    reason=(
        "Seems to require reading from stdin? Works with the -s flag, but other "
        "tests might not."
    )
)
def test_run_remote(remote: _MockRemote):  # noqa: F811
    command = "echo OK"
    some_message = "BOBOBOB"
    remote.expect(
        host="login.server.mila.quebec",
        user=unittest.mock.ANY,
        port=2222,
        commands=[Command(command, out=some_message.encode())],
    )
    r = Remote("mila")
    result = r.run(command)
    assert result.stdout == some_message


@disable_internet_access
@pytest.mark.parametrize("keepalive", [0, 123])
@pytest.mark.parametrize("host", ["mila", "bob"])
def test_init(
    keepalive: int,
    host: str,
    monkeypatch: pytest.MonkeyPatch,
):
    """

    TODO: This test should show exactly what the behaviour of init
    is so we can isolate it from other tests and avoid having to write versions
    of other tests based on if the connection was passed or not to the
    constructor.
    """
    import milatools.cli.remote

    # If a class is used as a spec then the return value of the mock (the
    # instance of the class) will have the same spec. You can use a class as
    # the spec for an instance object by passing instance=True.
    # The returned mock will only be callable if instances of the mock are
    # callable.
    MockConnection: Mock = create_autospec(Connection, spec_set=True)
    monkeypatch.setattr(milatools.cli.remote, "Connection", MockConnection)
    mock_connection: Mock = MockConnection.return_value
    mock_transport: Mock = create_autospec(
        paramiko.Transport, spec_set=True, instance=True
    )
    mock_connection.transport = mock_transport

    r = Remote(host, keepalive=keepalive)

    MockConnection.assert_called_once_with(host)
    # The Remote should have created a Connection instance (which happens to be
    # the mock_connection we already have above.)
    assert r.connection is mock_connection

    if keepalive:
        mock_connection.open.assert_called_once()
        mock_transport.set_keepalive.assert_called_once_with(keepalive)
    else:
        mock_connection.open.assert_not_called()
        mock_transport.set_keepalive.assert_not_called()


@pytest.fixture
def host() -> str:
    return "mila"


@pytest.fixture
def mock_connection(host: str, mocker: pytest_mock.MockerFixture) -> Connection | Mock:
    if RUN_COMMANDS_FOR_REAL:
        return Connection(host)
    MockConnection: Mock = create_autospec(
        Connection, spec_set=True, _name="MockConnection"
    )
    mock_connection: Mock = MockConnection(host)
    # mock_connection.configure_mock(name="mock_connection")
    MockTransport: Mock = create_autospec(
        paramiko.Transport, spec_set=True, _name="MockTransport"
    )
    mock_transport: Mock = MockTransport.return_value
    # mock_transport.configure_mock(name="mock_transport")
    mock_connection.transport = mock_transport
    mocker.patch("paramiko.Transport", MockTransport)
    return mock_connection
    # return mock_connection


@can_run_for_real
def test_with_transforms(
    mock_connection: Connection | Mock,
    host: str,
):
    r = Remote(host, connection=mock_connection)
    r = r.with_transforms(
        lambda cmd: f"echo 'this is printed before the command' && {cmd}"
    )
    result = r.run("echo OK", display=None, hide=False, warn=False, asynchronous=False)
    if RUN_COMMANDS_FOR_REAL:
        assert result.stdout == "this is printed before the command\nOK\n"
        assert result.stderr == ""
    else:
        mock_connection.run.assert_called_once_with(
            "echo 'this is printed before the command' && echo OK",
            asynchronous=False,
            hide=False,
            out_stream=None,
            warn=False,
        )
        mock_connection.local.assert_not_called()


@can_run_for_real
def test_wrap(mock_connection: Connection | Mock, host: str):
    r = Remote(host, connection=mock_connection)
    command = "echo OK"
    template = "echo 'hello, this is the command: <{}>'"
    command_output = "hello, this is the command: <echo OK>"
    r = r.wrap(template)
    result = r.run(
        command,
        display=None,
        hide=False,
        warn=False,
        asynchronous=False,
    )
    if RUN_COMMANDS_FOR_REAL:
        assert result.stdout == command_output + "\n"
        assert result.stderr == ""
    else:
        mock_connection.run.assert_called_once_with(
            template.format(command),
            asynchronous=False,
            hide=False,
            warn=False,
            out_stream=None,
        )
        mock_connection.local.assert_not_called()


@can_run_for_real
def test_with_precommand(mock_connection: Connection | Mock, host: str):
    r = Remote(host, connection=mock_connection)
    precommand = "echo BEFORE"
    some_command = "hostname"
    r = r.with_precommand(precommand)
    result = r.run(
        some_command,
        display=None,
        hide=False,
        warn=False,
        asynchronous=False,
    )
    if RUN_COMMANDS_FOR_REAL:
        # TODO: write this in a way that isn't as specific to the mila cluster.
        # The actual output looks like this:
        # assert result.stdout == "BEFORE\nlogin-1\n"
        before_n_login, dash, number_n = result.stdout.partition("-")
        assert before_n_login == "BEFORE\nlogin"
        assert dash == "-"
        assert number_n.endswith("\n")
        assert number_n.rstrip().isdigit()
    else:
        mock_connection.run.assert_called_once_with(
            f"{precommand} && {some_command}",
            asynchronous=False,
            hide=False,
            warn=False,
            out_stream=None,
        )
        mock_connection.local.assert_not_called()


@dont_run_for_real
def test_with_profile(mock_connection: Connection | Mock, host: str):
    r = Remote(host, connection=mock_connection)
    some_command = "whoami"
    profile = "my_profile"  # this gets sourced.
    r = r.with_profile(profile)
    _ = r.run(
        some_command,
        display=None,
        hide=False,
        warn=False,
        out_stream=None,
        asynchronous=False,
    )
    mock_connection.run.assert_called_once_with(
        f"source {profile} && {some_command}",
        asynchronous=False,
        hide=False,
        warn=False,
        out_stream=None,
    )
    mock_connection.local.assert_not_called()


@can_run_for_real
def test_with_bash(mock_connection: Connection | Mock, host: str):
    r = Remote(host, connection=mock_connection)
    some_command = "echo hello my name is bob"
    r = r.with_bash()
    result = r.run(
        some_command,
        display=None,
        hide=False,
        warn=False,
        out_stream=None,
        asynchronous=False,
    )
    if RUN_COMMANDS_FOR_REAL:
        assert result.command == f"bash -c '{some_command}'"
        assert result.stdout == "hello my name is bob\n"
        assert result.stderr == ""
    else:
        mock_connection.run.assert_called_once_with(
            shlex.join(["bash", "-c", some_command]),
            asynchronous=False,
            hide=False,
            warn=False,
            out_stream=None,
        )
        mock_connection.local.assert_not_called()


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
        f"\x1b[1m\x1b[36m({host}) $ {message}\x1b(B\x1b[m\n",
        f"({host}) $ {message}\n",
    )


@dont_run_for_real
@disable_internet_access
@pytest.mark.parametrize("asynchronous", [True, False])
@pytest.mark.parametrize("hide", [True, False])
@pytest.mark.parametrize("warn", [True, False])
def test_run(
    mock_connection: Connection,
    host: str,
    asynchronous: bool,
    hide: bool,
    warn: bool,
):
    command = "echo OK"
    command_output = "bob"
    r = Remote(host, connection=mock_connection)
    mock_promise = create_autospec(
        invoke.runners.Promise,
        spec_set=True,
        instance=True,
    )
    mock_promise.join.return_value = (
        Mock(wraps=invoke.runners.Result(stdout=command_output), spec_set=True),
    )
    mock_connection.run.return_value = mock_promise

    output = r.run(
        command, display=None, hide=hide, warn=warn, asynchronous=asynchronous
    )
    mock_connection.run.assert_called_once_with(
        command, asynchronous=asynchronous, hide=hide, warn=warn, out_stream=None
    )
    mock_promise.join.assert_called_once()
    mock_connection.local.assert_not_called()
    assert output == mock_promise


@can_run_for_real
@pytest.mark.parametrize("hide", [True, False])
@pytest.mark.parametrize("warn", [True, False])
def test_get_output(mock_connection: Connection, host: str, hide: bool, warn: bool):
    command = "echo OK"
    r = Remote(host, connection=mock_connection)
    if RUN_COMMANDS_FOR_REAL:
        output = r.get_output(command, display=None, hide=hide, warn=warn)
        assert output == "OK"
    else:
        command_output = "bob"
        mock_connection.run.return_value = invoke.runners.Result(stdout=command_output)
        output = r.get_output(command, display=None, hide=hide, warn=warn)
        mock_connection.run.assert_called_once_with(
            command, asynchronous=False, hide=hide, out_stream=None, warn=warn
        )
        assert output == command_output


# TODO: If `hide==True`, then we should not see anything in either stdout or stderr.
# Need to add a fixture or something else that actually checks for that.


@pytest.fixture(params=[True, False])
def hide(
    request: pytest.FixtureRequest, capsys: pytest.CaptureFixture
) -> Generator[bool, None, None]:
    value: bool = request.param

    yield value
    if value:
        output = capsys.readouterr()
        assert output.out == ""
        assert output.err == ""


@dont_run_for_real
@pytest.mark.xfail(
    reason=(
        "BUG: Seems like either a bug or the name is misleading! `get_lines` "
        "splits the output based on spaces, not lines!"
    ),
    strict=True,
    raises=AssertionError,
)
@pytest.mark.parametrize("hide", [True, False])
@pytest.mark.parametrize("warn", [True, False])
def test_get_lines(mock_connection: Connection, host: str, hide: bool, warn: bool):
    command = "echo 'Line 1 has this value' && echo 'Line 2 has this other value'"
    command_output_lines = [
        "Line 1 has this value",
        "Line 2 has this other value",
    ]
    command_output = "\n".join(command_output_lines)
    mock_connection.run.return_value = invoke.runners.Result(stdout=command_output)
    r = Remote(host, connection=mock_connection)
    lines = r.get_lines(command, hide=hide, warn=warn)
    assert lines == command_output_lines


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
    """TODO: It's very hard to write this test in such a way where it doesn't just test
    itself...
    """

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
        warn: bool,
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
        asynchronous=False,
        hide=True,
        warn=False,
        out_stream=None,
    )
    mock_connection.put.assert_called_once_with(unittest.mock.ANY, dest)


@can_run_for_real
def test_home(mock_connection: Connection, host: str):
    r = Remote(host, connection=mock_connection)
    home_dir = r.home()
    if RUN_COMMANDS_FOR_REAL:
        assert home_dir.startswith("/home/mila/")
    else:
        mock_connection.run.assert_called_once_with(
            "echo $HOME", asynchronous=False, hide=True, warn=False, out_stream=None
        )
        mock_connection.local.assert_not_called()


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
        # Transforms aren't used here. Seems a bit weird for this to be a public method
        # then, no?
        assert remote.srun_transform("bob") == "srun --time=00:01:00 bash -c bob"

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
        original_persist: bool = False
        remote = SlurmRemote(
            mock_connection,
            alloc=alloc,
            transforms=transforms,
            persist=original_persist,
        )
        new_transforms = [some_other_transform]
        transformed = remote.with_transforms(*new_transforms, persist=persist)
        # NOTE: Feels dumb to do this. Not sure what I should be doing otherwise.
        assert transformed.connection == remote.connection
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

        # TODO: Not sure if this test has any use at this point..
        remote.extract = Mock(
            spec=remote.extract,
            spec_set=True,
            return_value=(
                Mock(spec=invoke.runners.Runner, spec_set=True),
                {"node_name": "bob", "jobid": "1234"},
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
        assert results == {"node_name": "bob", "jobid": "1234"}
        # raise NotImplementedError("TODO: Imporant and potentially complicated test")

    @dont_run_for_real
    @disable_internet_access
    def test_ensure_allocation_without_persist(self, mock_connection: Connection):
        alloc = ["--time=00:01:00"]
        transforms = [some_transform]
        remote = SlurmRemote(
            mock_connection, alloc=alloc, transforms=transforms, persist=False
        )

        def write_stuff(
            command: str,
            asynchronous: bool,
            hide: bool,
            warn: bool,
            pty: bool,
            out_stream: QueueIO,
        ):
            assert command == f"bash -c 'salloc {shlex.join(alloc)}'"
            out_stream.write("salloc: Nodes bob-123 are ready for job")
            return unittest.mock.DEFAULT

        mock_connection.run.side_effect = write_stuff
        results, runner = remote.ensure_allocation()

        mock_connection.run.assert_called_once_with(
            f"bash -c 'salloc {shlex.join(alloc)}'",
            hide=False,
            asynchronous=True,
            out_stream=unittest.mock.ANY,
            pty=True,
            warn=False,
        )
        assert results == {"node_name": "bob-123"}
        # raise NotImplementedError("TODO: Imporant and potentially complicated test")


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
