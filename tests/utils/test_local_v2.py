from __future__ import annotations

import pytest

from milatools.utils.local_v2 import LocalV2
from milatools.utils.runner import Runner

from .runner_tests import RunnerTests


class TestLocalV2(RunnerTests):
    @pytest.fixture(scope="class")
    def runner(self) -> Runner:
        return LocalV2()
