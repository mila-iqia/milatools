import functools
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from prompt_toolkit.input.defaults import create_pipe_input

from milatools.cli.profile import _ask_name, qn

from .common import output_tester


@pytest.mark.skip(reason="Test does not appear to be fully deterministic")
def test__ask_name(capsys, file_regression):
    @contextmanager
    def _qn_text(texts: list):
        with create_pipe_input() as inp:
            with patch("questionary.text", new=functools.partial(qn.text, input=inp)):
                while texts:
                    inp.send_text(texts.pop(0))
                yield

    def _test():
        with _qn_text(["test_file name\n"]):
            result = _ask_name("ask a name")
            assert result == "test_file name"
        with _qn_text(["///\n", "test/filename\n"]):
            result = _ask_name("reask a wrong name")
            assert result == "test/filename"

        return (None, None)

    output_tester(_test, capsys, file_regression)
