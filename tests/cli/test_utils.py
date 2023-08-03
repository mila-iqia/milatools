import functools
import random
import socket
from unittest.mock import patch

import pytest
from prompt_toolkit.input.defaults import create_pipe_input

from milatools.cli.utils import get_fully_qualified_name, qn, randname, yn


def test_randname(file_regression):
    random.seed(0)
    file_regression.check("\n".join(randname() for _ in range(100)))


@pytest.mark.skip(reason="Fails when test_profile::test__ask_name runs.")
def test_yn():
    with create_pipe_input() as inp:
        with patch("questionary.confirm", new=functools.partial(qn.confirm, input=inp)):
            inp.send_text("Y")
            result = yn("????")
            assert result == True

    with create_pipe_input() as inp:
        with patch("questionary.confirm", new=functools.partial(qn.confirm, input=inp)):
            inp.send_text("n")
            result = yn("????")
            assert result == False


def test_hostname():
    assert get_fully_qualified_name()
