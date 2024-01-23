from __future__ import annotations

import os

import pytest

from .common import REQUIRES_S_FLAG_REASON

in_github_ci = "PLATFORM" in os.environ


@pytest.fixture(autouse=in_github_ci)
def skip_if_s_flag_passed_during_ci_run_and_test_doesnt_require_it(
    request: pytest.FixtureRequest, pytestconfig: pytest.Config
):
    capture_value = pytestconfig.getoption("-s")
    assert capture_value in ["no", "fd"]
    s_flag_set = capture_value == "no"
    test_requires_s_flag = any(
        mark.name == "skipif"
        and mark.kwargs.get("reason", "") == REQUIRES_S_FLAG_REASON
        for mark in request.node.iter_markers()
    )
    if s_flag_set and not test_requires_s_flag:
        # NOTE: WE only run the tests that require -s when -s is passed, because
        # otherwise we get very weird errors related to closed file descriptors!
        pytest.skip(reason="Running with the -s flag and this test doesn't require it.")
