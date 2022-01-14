import importlib
import shutil
from pathlib import Path
from typing import Tuple, Type

import numpy as np
import torch
import pytest
import torchvision.datasets as tvd
from torchvision.datasets import VisionDataset

import milavision.datasets as mvd
from milavision._utils import ClusterType

from .conftest import only_runs_locally, only_runs_on_mila_cluster

current_env = ClusterType.current()


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
        data_dir = ClusterType.MILA.fast_data_dir / "_temp_data"
        data_dir.mkdir(exist_ok=False, parents=False)
        yield data_dir
        shutil.rmtree(data_dir, ignore_errors=False)


import os


@only_runs_on_mila_cluster
class TestMila:
    @pytest.mark.parametrize(
        "dataset_type", [mvd.MNIST, mvd.CIFAR10, mvd.CIFAR100], ids=lambda c: c.args[0].__name__
    )
    def test_root_is_ignored(
        self, temp_root_dir: Path, dataset_type: Type[VisionDataset], monkeypatch
    ):
        monkeypatch.setitem(os.environ, "SLURM_TMPDIR", str(temp_root_dir))
        d = dataset_type(str(temp_root_dir / "blabla"))
        assert not (temp_root_dir / "blabla").exists()

    @pytest.mark.parametrize(
        "dataset_types",
        [(tvd.MNIST, mvd.MNIST), (tvd.CIFAR10, mvd.CIFAR10), (tvd.CIFAR100, mvd.CIFAR100),],
        ids=lambda t: t[0].__name__,
    )
    def test_simple_dataset(
        self, temp_root_dir: Path, dataset_types: Tuple[Type[VisionDataset], Type[VisionDataset]]
    ):
        """ Test that we can create this dataset quite simply, like we usually would. """
        # NOTE: This test is loading the datasets from `$SLURM_TMPDIR` that are moved there by the
        # previous tests!
        tv_dataset_class, mv_dataset_class = dataset_types
        mv_dataset = mv_dataset_class(str(temp_root_dir))
        tv_dataset = tv_dataset_class(str(current_env.torchvision_dir))
        assert len(tv_dataset) == len(mv_dataset)
        tv_first_item = tv_dataset[0]
        mv_first_item = mv_dataset[0]
        for tv_value, mv_value in zip(tv_first_item, mv_first_item):
            if isinstance(tv_value, (torch.Tensor, np.ndarray)):
                assert (tv_value == mv_value).all()
            else:
                assert tv_value == mv_value
