import functools
from contextlib import contextmanager
from subprocess import CompletedProcess
from unittest.mock import patch

from prompt_toolkit.input.defaults import create_pipe_input

from milatools.cli.profile import _ask_name, qn


def _test_stdouterr(func, capsys, file_regression):
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
            f"{captured.out}:::::\n{captured.err}=====\n{out}^^^^^\n{err}^^^^^\n"
        )


def test__ask_name(capsys, file_regression):
    @contextmanager
    def _qn_text(texts: list):
        with create_pipe_input() as inp:
            with patch("questionary.text", new=functools.partial(qn.text, input=inp)):
                while texts:
                    inp.send_text(texts.pop(0))
                yield

    def _test():
        with _qn_text(["test_file name\r"]):
            result = _ask_name("ask a name")
            assert result == "test_file name"
        with _qn_text(["///\r", "test/filename\r"]):
            result = _ask_name("reask a wrong name")
            assert result == "test/filename"

        return (None, None)

    _test_stdouterr(_test, capsys, file_regression)
