from __future__ import annotations

import pytest

from milatools.utils.local import Local
from milatools.utils.runner import Runner

from .runner_tests import RunnerTests


class TestLocal(RunnerTests):
    @pytest.fixture(scope="class")
    def runner(self) -> Runner:
        return Local()
