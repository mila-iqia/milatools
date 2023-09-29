from __future__ import annotations

import shlex
import unittest
import unittest.mock
from typing import Callable
from unittest.mock import ANY, Mock, create_autospec

import invoke
import paramiko
import pytest
import pytest_mock
from fabric.testing.base import Command, MockChannel, Session
from fabric.testing.fixtures import remote  # noqa
from fabric.testing.fixtures import Connection, MockRemote  # client,
from pytest_regressions.file_regression import FileRegressionFixture
from typing_extensions import ParamSpec

from milatools.cli.remote import QueueIO, Remote, get_first_node_name

no_internet = pytest.mark.disable_socket

# Disable internet access for all unit tests here to prevent false positives
pytestmark = no_internet

P = ParamSpec("P")


class _MockRemote(MockRemote):
    # NOTE: This class isn't actually used. It's just here so we can see what
    # the signature of the test methods are.
    def expect(
        self,
        _session_fn: Callable[P, Session] = Session,
        *session_args: P.args,
        **session_kwargs: P.kwargs,
    ) -> MockChannel:
        return super().expect(*session_args, **session_kwargs)


@pytest.mark.skip(
    reason="Seems to require reading from stdin? Works with the -s flag, but other tests don't"
)
def test_run_remote(remote: _MockRemote):
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


@no_internet
@pytest.mark.parametrize("keepalive", [0, 123])
@pytest.mark.parametrize("host", ["mila", "bob"])
def test_init(
    keepalive: int,
    host: str,
    monkeypatch: pytest.MonkeyPatch,
    mocker: pytest_mock.MockerFixture,
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


# TODO: Make it possible to run the tests "for real" (i.e. really connecting to Mila) using a flag
@pytest.fixture
def mock_connection(host: str, mocker: pytest_mock.MockerFixture):
    MockConnection: Mock = create_autospec(Connection, spec_set=True)
    mock_connection = MockConnection(host)
    MockTransport: Mock = create_autospec(paramiko.Transport, spec_set=True)
    mock_transport: Mock = MockTransport.return_value
    mock_connection.transport = mock_transport
    mocker.patch("paramiko.Transport", MockTransport)
    yield mock_connection
    # return mock_connection


@no_internet
def test_with_transforms(mock_connection: Connection | Mock, host: str):
    r = Remote(host, connection=mock_connection)
    r = r.with_transforms(lambda cmd: f"bash -c {cmd}")
    _ = r.run("echo OK", display=None, hide=False, warn=False, asynchronous=False)
    mock_connection.run.assert_called_once_with(
        "bash -c echo OK", asynchronous=False, hide=False, warn=False
    )
    mock_connection.local.assert_not_called()


def test_wrap(mock_connection: Connection | Mock, host: str):
    r = Remote(host, connection=mock_connection)
    r = r.wrap("hello, this is the command: <{}>")
    _ = r.run("echo OK", display=None, hide=False, warn=False, asynchronous=False)
    mock_connection.run.assert_called_once_with(
        "hello, this is the command: <echo OK>",
        asynchronous=False,
        hide=False,
        warn=False,
    )
    mock_connection.local.assert_not_called()


def test_with_precommand(mock_connection: Connection | Mock, host: str):
    r = Remote(host, connection=mock_connection)
    some_command = "hostname"
    precommand = "echo BEFORE"
    r = r.with_precommand(precommand)
    _ = r.run(some_command, display=None, hide=False, warn=False, asynchronous=False)
    mock_connection.run.assert_called_once_with(
        f"{precommand} && {some_command}", asynchronous=False, hide=False, warn=False
    )
    mock_connection.local.assert_not_called()


def test_with_profile(mock_connection: Connection | Mock, host: str):
    r = Remote(host, connection=mock_connection)
    some_command = "whoami"
    profile = "my_profile"
    r = r.with_profile(profile)
    _ = r.run(some_command, display=None, hide=False, warn=False, asynchronous=False)
    mock_connection.run.assert_called_once_with(
        f"source {profile} && {some_command}",
        asynchronous=False,
        hide=False,
        warn=False,
    )
    mock_connection.local.assert_not_called()


def test_with_bash(mock_connection: Connection | Mock, host: str):
    r = Remote(host, connection=mock_connection)
    some_command = "echo 'hello my name is $USER'"
    r = r.with_bash()
    _ = r.run(some_command, display=None, hide=False, warn=False, asynchronous=False)
    mock_connection.run.assert_called_once_with(
        shlex.join(["bash", "-c", some_command]),
        asynchronous=False,
        hide=False,
        warn=False,
    )
    mock_connection.local.assert_not_called()


def test_display(
    mock_connection: Connection | Mock,
    host: str,
    capsys: pytest.CaptureFixture,
):
    some_message = "foo"
    r = Remote(host, connection=mock_connection)
    r.display(some_message)
    output = capsys.readouterr().out
    assert output == f"({host}) $ {some_message}\n"
    # file_regression.check(output)


# def test_simple_run(
#     mock_connection: Connection | Mock,
#     host: str,
#     mocker: pytest_mock.MockerFixture,
# ):
#     # patcher.patch
#     command = "echo OK"
#     command_output = "BOBOBOBO"

#     r = Remote(host, connection=mock_connection)
#     result = r.simple_run(command)
#     assert result == command_output
#     mock_connection.run.assert_called_once_with(
#         command,
#         asynchronous=False,
#         hide=True,
#         warn=False,
#     )
#     mock_connection.local.assert_not_called()


@no_internet
@pytest.mark.parametrize("asynchronous", [True, False])
@pytest.mark.parametrize("hide", [True, False])
@pytest.mark.parametrize("warn", [True, False])
def test_run(
    mock_connection: Connection, host: str, asynchronous: bool, hide: bool, warn: bool
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
        command, asynchronous=asynchronous, hide=hide, warn=warn
    )
    mock_connection.local.assert_not_called()
    assert output == mock_promise


@no_internet
@pytest.mark.parametrize("hide", [True, False])
@pytest.mark.parametrize("warn", [True, False])
def test_get_output(mock_connection: Connection, host: str, hide: bool, warn: bool):
    command = "echo OK"
    command_output = "bob"
    r = Remote(host, connection=mock_connection)
    mock_connection.run.return_value = invoke.Result(stdout=command_output)
    output = r.get_output(command, display=None, hide=hide, warn=warn)
    mock_connection.run.assert_called_once_with(
        command, asynchronous=False, hide=hide, warn=warn
    )
    assert output == command_output


# TODO: If `hide==True`, then we should not see anything in either stdout or stderr. Need to add
# a fixture or something else that actually checks for that.
@pytest.mark.xfail(
    reason="Seems like the name is a little bit deceptive. Splits based on spaces, not lines!",
    strict=True,
)
@pytest.mark.parametrize("hide", [True, False])
@pytest.mark.parametrize("warn", [True, False])
def test_get_lines(mock_connection: Connection, host: str, hide: bool, warn: bool):
    command = "echo OK"
    command_output_lines = ["Line 1 has this value", "Line 2 has this other value"]
    command_output = "\n".join(command_output_lines)
    mock_connection.run.return_value = invoke.Result(stdout=command_output)
    r = Remote(host, connection=mock_connection)
    lines = r.get_lines(command, hide=hide, warn=warn)
    assert lines == command_output_lines


# def run(
#     self,

# def get_output(
#     self,

# def get_lines(
#     self,

# def extract(
#     self,

# def get(self, src: str, dest: str | None) -> fabric.transfer.Result:
#     return self.connection.get(src, dest)

# def put(self, src: str | Path, dest: str) -> fabric.transfer.Result:
#     return self.connection.put(src, dest)

# def puttext(self, text: str, dest: str) -> None:
#     base = Path(dest).parent

# def home(self) -> str:
#     return self.get_output("echo $HOME", hide=True)

# def persist(self):
#     qn.print(

# def ensure_allocation(self) -> tuple[NodeNameDict, None]:
#     return {"node_name": self.hostname}, None

# def run_script(self, name: str, *args: str, **kwargs):
#     # TODO: This method doesn't seem to be used.

# def extract_script(
#     self,


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
