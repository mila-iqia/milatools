from __future__ import annotations

import pytest

enable_internet_access_flag = "--enable-internet"
_internet_access_mark_name = "can_use_internet"


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
