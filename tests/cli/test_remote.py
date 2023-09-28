from __future__ import annotations
from typing_extensions import ParamSpec
from unittest.mock import create_autospec

from milatools.cli.remote import QueueIO, Remote, get_first_node_name
from fabric.testing.fixtures import (
    Connection,
    MockRemote,
    # client,
)  # noqa
from unittest.mock import Mock
from fabric.testing.base import MockChannel, Session, Command
from pytest_regressions.file_regression import FileRegressionFixture
from typing import Callable
import pytest

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


def test_run_remote(remote: _MockRemote):
    some_message = "BOBOBOB"
    remote.expect(
        host="login.server.mila.quebec",
        user="normandf",
        port=2222,
        commands=[Command("echo OK", out=some_message.encode())],
    )
    r = Remote("mila")
    result = r.run("echo OK")
    assert result.stdout == some_message


@no_internet
@pytest.mark.parametrize("keepalive", [0, 123])
@pytest.mark.parametrize("host", ["mila", "bob"])
def test_init(keepalive: int, host: str, monkeypatch: pytest.MonkeyPatch):
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
    mock_connection: Mock = MockConnection()

    r = Remote(host, keepalive=keepalive)

    MockConnection.assert_called_once_with(host)
    # The Remote should have created a Connection instance (which happens to be
    # the mock_connection we already have above.)
    assert r.connection is mock_connection

    if keepalive:
        mock_connection.open.assert_called_once()
        mock_connection.transport.set_keepalive.assert_called_once_with(
            keepalive
        )
    else:
        mock_connection.open.assert_not_called()
        mock_connection.transport.set_keepalive.assert_not_called()


@no_internet
def test_run_with_connection():
    host = "mila"
    MockConnection = create_autospec(Connection, spec_set=True)
    mock_connection = MockConnection(host)
    c = mock_connection
    r = Remote(host, connection=c)
    _ = r.run("echo OK")
    c.run.assert_called_once_with(
        "echo OK", asynchronous=False, hide=False, warn=False
    )
    c.local.assert_not_called()


# class TestRemote:

# def test_init(self):
#     ...

# def test_with_transforms(self, *transforms: Callable[[str], str]) -> Self:
#     return Remote(

# def wrap(self, wrapper: str) -> Self:
#     return self.with_transforms(wrapper.format)

# def with_precommand(self, precommand: str) -> Self:
#     return self.wrap(f"{precommand} && {{}}")

# def with_profile(self, profile: str) -> Self:
#     return self.wrap(f"source {profile} && {{}}")

# def with_bash(self) -> Self:
#     return self.with_transforms(lambda cmd: shjoin(["bash", "-c", cmd]))

# def display(self, cmd: str) -> None:
#     print(T.bold_cyan(f"({self.hostname}) $ ", cmd))

# def _run(
#     self, cmd: str, hide: bool = False, warn: bool = False, **kwargs

# def simple_run(self, cmd: str):
#     return self._run(cmd, hide=True)

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
