from typing import Type
from milavision.envs import mila
from pathlib import Path
from milavision._utils import ClusterType
import pytest

current_env = ClusterType.current()
from .conftest import local, mila
import torchvision.datasets as tvd
from torchvision.datasets import VisionDataset


@pytest.fixture(scope="module")
def root_dir(tmp_path_factory):
    if current_env is ClusterType.LOCAL:
        return tmp_path_factory.mktemp("data")
    if current_env is ClusterType.MILA:
        return tmp_path_factory.mktemp("data")


@local
class TestLocal:
    def test_all_attributes_match(self):
        import torchvision
        import milavision

        matching_attributes = []
        for attribute, torchvision_value in vars(torchvision).items():
            if attribute.startswith("_"):
                continue
            assert hasattr(milavision, attribute)
            milavision_value = getattr(milavision, attribute)
            assert torchvision_value == milavision_value, attribute
            matching_attributes.append(attribute)
        assert False, ",".join(matching_attributes)
        # a bit too radical perhaps.
        # assert vars(torchvision) == vars(milavision)

    @pytest.mark.parametrize("dataset_type", [tvd.MNIST, tvd.CIFAR10, tvd.CIFAR100])
    def test_simple_dataset(self, dataset_type: Type[VisionDataset]):
        """ TODO: Test that we can create this dataset quite simply, like we usually would. """


@mila
class TestMila:
    def test_root_is_ignored(self, tmp_path: Path):
        from milavision.datasets import MNIST

        d = MNIST(tmp_path / "blabla")
        assert tmp_path.iterdir
