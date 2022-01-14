import importlib
import shutil
from pathlib import Path
from typing import Tuple, Type

import pytest
import torchvision.datasets as tvd
from torchvision.datasets import VisionDataset

import milavision.datasets as mvd
from milavision._utils import ClusterType

from .conftest import only_runs_locally, only_runs_on_mila_cluster

current_env = ClusterType.current()

import torchvision.datasets


@only_runs_locally
class TestLocal:
    def test_all_attributes_match(self):

        import torchvision.datasets
        import milavision.datasets

        matching_attributes = []
        for attribute, torchvision_value in vars(torchvision.datasets).items():
            if attribute.startswith("__") and attribute.endswith("__"):
                continue
            assert hasattr(milavision.datasets, attribute)
            milavision_value = getattr(milavision.datasets, attribute)
            # Check that the attributes from both modules point to *exactly the same objects*.
            assert torchvision_value is milavision_value, attribute
            matching_attributes.append(attribute)
        # a bit too radical perhaps.
        # assert vars(torchvision) == vars(milavision)


@pytest.fixture()
def temp_root_dir(tmp_path_factory):
    if current_env is ClusterType.LOCAL:
        return tmp_path_factory.mktemp("data")

    if current_env is ClusterType.MILA:
        from milavision.envs.mila import SLURM_TMPDIR

        data_dir = SLURM_TMPDIR / "_temp_data"
        data_dir.mkdir(exist_ok=False, parents=False)
        yield data_dir
        shutil.rmtree(data_dir, ignore_errors=False)


@only_runs_on_mila_cluster
class TestMila:
    @pytest.mark.parametrize("dataset_type", [tvd.MNIST, tvd.CIFAR10, tvd.CIFAR100])
    def test_root_is_ignored(self, temp_root_dir: Path, dataset_type: Type[VisionDataset]):
        d = MNIST(temp_root_dir / "blabla")
        assert not (temp_root_dir / "blabla").exists()

    @pytest.mark.parametrize(
        "dataset_types",
        [(tvd.MNIST, mvd.MNIST), (tvd.CIFAR10, mvd.CIFAR10), (tvd.CIFAR100, mvd.CIFAR100),],
    )
    def test_simple_dataset(
        self, temp_root_dir: Path, dataset_types: Tuple[Type[VisionDataset], Type[VisionDataset]]
    ):
        """ Test that we can create this dataset quite simply, like we usually would. """
        tv_dataset_class, mv_dataset_class = dataset_types
        tv_dataset = tv_dataset_class(str(current_env.torchvision_dir))
        mv_dataset = mv_dataset_class(str(current_env.torchvision_dir))
        assert len(tv_dataset) == len(mv_dataset)
        assert (tv_dataset[0][0] == mv_dataset[0][0]).all()
        assert (tv_dataset[0][1] == mv_dataset[0][1]).all()
