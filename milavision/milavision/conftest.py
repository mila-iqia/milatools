from milavision._utils import ClusterType
import pytest

current_env = ClusterType.current()


def local(t):
    return pytest.mark.skipif(
        current_env is not ClusterType.LOCAL, reason="Only runs when not on a cluster."
    )(t)


def mila(t):
    return pytest.mark.skipif(
        current_env is not ClusterType.MILA, reason="Only runs on the Mila cluster."
    )(t)

