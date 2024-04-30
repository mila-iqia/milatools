import functools
import multiprocessing
import random
from pathlib import Path
from unittest.mock import patch

import pytest
from prompt_toolkit.input.defaults import create_pipe_input

from milatools.cli.utils import (
    get_fully_qualified_name,
    get_hostname_to_use_for_compute_node,
    make_process,
    qn,
    randname,
    yn,
)


def test_randname(file_regression):
    random.seed(0)
    file_regression.check("\n".join(randname() for _ in range(100)) + "\n")


@pytest.mark.skip(reason="Fails when test_profile::test__ask_name runs.")
def test_yn():
    with create_pipe_input() as inp:
        with patch("questionary.confirm", new=functools.partial(qn.confirm, input=inp)):
            inp.send_text("Y")
            result = yn("????")
            assert result is True

    with create_pipe_input() as inp:
        with patch("questionary.confirm", new=functools.partial(qn.confirm, input=inp)):
            inp.send_text("n")
            result = yn("????")
            assert result is False


def test_hostname():
    assert get_fully_qualified_name()


@pytest.mark.parametrize(
    ("cluster_name", "node", "expected"),
    [
        ("mila", "cn-a001", "cn-a001.server.mila.quebec"),
    ]
    + [
        # Host !beluga  bc????? bg????? bl?????
        ("beluga", cnode, cnode)
        for prefix in ["bc", "bg", "bl"]
        for cnode in [f"{prefix}12345"]
    ]
    + [
        # Host !cedar   cdr? cdr?? cdr??? cdr????
        ("cedar", cnode, cnode)
        for n in range(5)
        for cnode in [f"cdr{'0' * n}"]
    ]
    + [
        # Host !graham  gra??? gra????
        ("cedar", cnode, cnode)
        for n in range(3, 5)
        for cnode in [f"cdr{'0' * n}"]
    ]
    + [
        # Host !narval  nc????? ng?????
        ("beluga", cnode, cnode)
        for prefix in ["nc", "ng"]
        for cnode in [f"{prefix}12345"]
    ],
    # NOTE: Not including niagara for now, since DRAC users don't automatically get
    # access to it (plus, it doesn't have GPUs).
    # + [
    #     # Host !niagara nia????
    #     ("niagara", cnode, cnode)
    #     for cnode in ["nia1234"]
    # ],
)
def test_get_hostname_to_use_for_compute_node(
    cluster_name: str,
    node: str,
    expected: str,
    ssh_config_file: Path,
):
    assert (
        get_hostname_to_use_for_compute_node(
            node_name=node, cluster=cluster_name, ssh_config_path=ssh_config_file
        )
        == expected
    )


def test_get_fully_qualified_hostname_of_compute_node_unknown_cluster(
    ssh_config_file: Path,
):
    node_name = "some-node"
    with pytest.warns(UserWarning):
        assert (
            get_hostname_to_use_for_compute_node(
                node_name=node_name,
                cluster="unknown-cluster",
                ssh_config_path=ssh_config_file,
            )
            == node_name
        )


def test_make_process():
    process = make_process(print, "hello", end="!")
    assert isinstance(process, multiprocessing.Process)
    # TODO: Make the process daemonic again (if needed), for now we want to be able to
    # run the syncing of vscode extensions in the background during `mila code`.
    assert not process.daemon
    assert not process.is_alive()
