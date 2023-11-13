from __future__ import annotations

from subprocess import CompletedProcess
from typing import Callable

import pytest
from pytest_regressions.file_regression import FileRegressionFixture

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
