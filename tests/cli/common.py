from __future__ import annotations

import functools
import inspect
import os
import sys
import typing
from collections.abc import Callable
from subprocess import CompletedProcess
from typing import Any

import fabric
import paramiko.ssh_exception
import pytest
from pytest_regressions.file_regression import FileRegressionFixture
from typing_extensions import ParamSpec

if typing.TYPE_CHECKING:
    from typing import TypeGuard

in_github_CI = all(var in os.environ for var in ["CI", "GITHUB_ACTION", "GITHUB_ENV"])
"""True if this is being run inside the GitHub CI."""

skip_if_on_github_CI = pytest.mark.skipif(
    in_github_CI, reason="This test shouldn't run on the Github CI."
)
skip_param_if_on_github_ci = functools.partial(pytest.param, marks=skip_if_on_github_CI)


passwordless_ssh_connection_to_localhost_is_setup = False

try:
    _connection = fabric.Connection("localhost")
    _connection.open()
except (
    paramiko.ssh_exception.SSHException,
    paramiko.ssh_exception.NoValidConnectionsError,
):
    pass
else:
    passwordless_ssh_connection_to_localhost_is_setup = True
    _connection.close()

requires_ssh_to_localhost = pytest.mark.skipif(
    not passwordless_ssh_connection_to_localhost_is_setup,
    reason="Test requires a SSH connection to localhost.",
)


REQUIRES_S_FLAG_REASON = (
    "Seems to require reading from stdin? Works with the -s flag, but other "
    "tests might not."
)
requires_s_flag = pytest.mark.skipif(
    "-s" not in sys.argv,
    reason=REQUIRES_S_FLAG_REASON,
)
requires_no_s_flag = pytest.mark.skipif(
    "-s" in sys.argv,
    reason="Passing pytest's -s flag makes this test fail.",
)
on_windows = sys.platform == "win32"
in_github_windows_ci = in_github_CI and os.environ.get("PLATFORM") == "windows-latest"

P = ParamSpec("P")


def xfails_on_windows(
    raises: type[Exception] | tuple[type[Exception], ...] = (),
    strict: bool = False,
    reason: str = "TODO: Test doesn't work when running on Windows in the GitHub CI.",
    in_CI_only: bool = False,
):
    if in_github_windows_ci:
        assert sys.platform == "win32", sys.platform

    condition = on_windows
    if in_CI_only:
        condition = condition and in_github_windows_ci
    return pytest.mark.xfail(
        condition,
        reason=reason,
        strict=strict,
        raises=raises,
    )


cmdtest = """===============
Captured stdout
===============
{cout}
===============
Captured stderr
===============
{cerr}
=============
Result stdout
=============
{out}
=============
Result stderr
=============
{err}
"""


def output_tester(
    func: Callable[
        [],
        tuple[str | CompletedProcess[str] | None, str | CompletedProcess[str] | None],
    ],
    capsys: pytest.CaptureFixture,
    file_regression: FileRegressionFixture,
):
    # TODO: Rework this, or add a proper docstring explaining what this does.
    out, err = None, None
    try:
        out, err = func()
        if isinstance(out, CompletedProcess):
            out, err = out.stdout, out.stderr
    finally:
        captured = capsys.readouterr()
        out = out if out else ""
        err = err if err else ""
        file_regression.check(
            cmdtest.format(cout=captured.out, cerr=captured.err, out=out, err=err)
        )


def function_call_string(
    fn: Callable[P, Any],
    *args: P.args,
    **kwargs: P.kwargs,
) -> str:
    """Returns a nice string representation of code that calls `fn(*args, **kwargs)`.

    This is used to show code snippets in the regression files generated by unit tests.
    """

    # Call `repr` on the arguments, except for lambdas, which are shown as their body.
    args_str = [_lambda_to_str(v) if _is_lambda(v) else repr(v) for v in args]
    kwargs_str = {
        k: _lambda_to_str(v) if _is_lambda(v) else repr(v) for k, v in kwargs.items()
    }
    fn_str = fn.__name__

    single_line = (
        fn_str
        + "("
        + ", ".join(args_str)
        + (", " if args_str and kwargs_str else "")
        + ", ".join(f"{k}={v}" for k, v in kwargs_str.items())
        + ")"
    )
    indent = 4 * " "
    multi_line = (
        f"{fn_str}(\n"
        + "\n".join(f"{indent}{arg}," for arg in args_str)
        + ("\n" if args_str and kwargs_str else "")
        + "\n".join(f"{indent}{key}={value}," for key, value in kwargs_str.items())
        + "\n)"
    )

    if len(single_line) < 80:
        return single_line
    return multi_line


def _is_lambda(v: Any) -> TypeGuard[Callable]:
    """Returns whether the value is a lambda expression."""
    return (
        callable(v)
        and isinstance(v, type(lambda _: _))
        and getattr(v, "__name__", None) == "<lambda>"
    )


def _lambda_to_str(lambda_: Callable) -> str:
    """Shows the body of the lambda instead of the default repr."""
    lambda_body = inspect.getsource(lambda_).strip()
    # when putting lambdas in a list, like so:
    # funcs = [
    #    lambda x: x + 1,
    # ]
    # a trailing comma is returned by `inspect.getsource`, which we want to remove.
    return _removesuffix(lambda_body, ",")


def _removesuffix(s: str, suffix: str) -> str:
    """Backport of `str.removesuffix` for Python<3.9."""
    if s.endswith(suffix):
        return s[: -len(suffix)]
    else:
        return s
