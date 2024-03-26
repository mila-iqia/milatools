from __future__ import annotations

import pytest

from milatools.cli.common import standard_server_v1, standard_server_v2

from ..conftest import launches_jobs

persist_or_not = pytest.mark.parametrize("persist", [True, False])


@launches_jobs
@persist_or_not
def test_standard_server_v1(cluster: str, allocation_flags: list[str], persist: bool):
    if cluster != "mila":
        pytest.skip(reason="Needs to be run on the Mila cluster for now.")
    standard_server_v1(
        path="bob",
        program="echo",
        installers={},
        command="ls",
        profile=None,
        persist=persist,
        port=None,
        name=None,
        node=None,
        job=None,
        alloc=allocation_flags,  # : list[str],
    )
    raise NotImplementedError("TODO: Add checks in this test.")


@launches_jobs
@persist_or_not
def test_standard_server_v2(cluster: str, allocation_flags: list[str], persist: bool):
    raise NotImplementedError("TODO: Design this test.")
    standard_server_v2(
        path="bob",
        program="echo",
        installers={},
        command="ls",
        profile=None,
        persist=persist,
        port=None,
        name=None,
        node=None,
        job=None,
        alloc=allocation_flags,
        cluster=cluster,  # type: ignore
    )


@launches_jobs
@persist_or_not
def test_mila_serve_notebook(cluster: str, allocation_flags: list[str]):
    raise NotImplementedError("TODO: Design this test.")
