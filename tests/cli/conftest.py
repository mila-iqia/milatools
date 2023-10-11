from __future__ import annotations
from typing import Literal

import pytest


# only_runs_for_real = pytest.mark.skipif(
#     not RUN_COMMANDS_FOR_REAL,
#     reason="Test needs to have real internet access to the cluster.",
# )
# disable_internet_access = pytest.mark.disable_socket
# dont_run_for_real = pytest.mark.skipif(
#     RUN_COMMANDS_FOR_REAL,
#     reason="Test shouldn't run with real internet access to the cluster.",
# )
# can_run_for_real = (
#     pytest.mark.enable_socket if RUN_COMMANDS_FOR_REAL else pytest.mark.disable_socket
# )
# pytestmark = pytest.mark.disable_socket

InternetAccess = Literal["local_only", "remote_only", "either"]
enable_internet_access_flag = "--enable-internet"


def pytest_addoption(parser: pytest.Parser):
    parser.addoption(
        enable_internet_access_flag,
        action="store_true",
        default=False,
        help=(
            "Allow certain specifically annotated tests to make real internet "
            "connections."
        ),
    )


_internet_access_mark_name = "can_use_internet"


def internet_access(requires: InternetAccess):
    """Marker that indicates whether a test can run with a real internet connection.

    - When the test is marked with `@internet_access("remote_only")`, it will only run
      when the `--enable-internet` flag is set.
    - When the test is marked with `@internet_access("local_only")`, it will only run
      when the `--enable-internet` flag is not set.
    - When the test is marked with `@internet_access("either")`, it can be run in either
      case. Note that you can use the `internet_enabled` fixture to check whether access
      to the internet is enabled or not.
    """
    return getattr(pytest.mark, _internet_access_mark_name)(requires)


def pytest_configure(config: pytest.Config):
    # register the "lvl" marker
    config.addinivalue_line(
        "markers",
        (
            f"{_internet_access_mark_name}(str): mark a test that runs either only "
            f"locally (no internet), remotely (real internet access), or both."
        ),
    )


@pytest.fixture
def internet_enabled(pytestconfig: pytest.Config) -> bool:
    internet_enabled = pytestconfig.getoption(enable_internet_access_flag)
    assert isinstance(internet_enabled, bool)
    return internet_enabled


def pytest_runtest_setup(item: pytest.Item):
    internet_enabled = item.config.getoption(enable_internet_access_flag)
    assert isinstance(internet_enabled, bool)
    can_use_internet_markers = list(item.iter_markers(name=_internet_access_mark_name))
    if can_use_internet_markers:
        can_use_internet_marker = can_use_internet_markers[0]
        mode: InternetAccess = can_use_internet_marker.args[0]
        if mode == "remote_only" and not internet_enabled:
            pytest.skip(
                f"test requires internet access, use {enable_internet_access_flag} "
                f"to enable."
            )
        if mode == "local_only" and internet_enabled:
            pytest.skip(
                f"test only runs locally (when {enable_internet_access_flag} is not "
                f"set)."
            )
    # NOTE: Not adding this here because it can cause issues with some test files
    # involving Questionary:
    # if not internet_enabled:
    #     item.add_marker(pytest.mark.disable_socket)
