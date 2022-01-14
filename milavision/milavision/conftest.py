from milavision._utils import ClusterType
import pytest

current_env = ClusterType.current()


def only_runs_locally(t):
    return pytest.mark.skipif(
        current_env is not ClusterType.LOCAL, reason="Only runs when not on a cluster."
    )(t)


def only_runs_on_mila_cluster(t):
    return pytest.mark.skipif(
        current_env is not ClusterType.MILA, reason="Only runs on the Mila cluster."
    )(t)

