import functools
import random
from unittest.mock import patch
from prompt_toolkit.input.defaults import create_pipe_input
from milatools.cli.utils import qn, randname, yn


def test_randname(file_regression):
    random.seed(0)
    file_regression.check("\n".join(randname() for _ in range(100)))


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
