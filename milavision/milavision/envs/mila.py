""" Set of functions for creating torchvision datasets when on the Mila cluster.

IDEA: later on, we could also add some functions for loading torchvision models from a cached
directory. 
"""
import inspect
import os
import shutil
import socket
from logging import getLogger as get_logger
from pathlib import Path
from typing import Dict, List, Optional, Type, Union

import torchvision.datasets as tvd
from torchvision.datasets import VisionDataset

from milavision._utils import VD, ClusterType

current_env = ClusterType.current()
# NOTE: It should always be possible to import this module even when not on the mila cluster.


""" a map of the files for each dataset type, relative to the torchvision dir. """
dataset_files: Dict[Type[VisionDataset], List[str]] = {
    tvd.MNIST: ["MNIST"],
    tvd.CIFAR10: ["cifar-10-batches-py"],
    tvd.CIFAR100: ["cifar-100-python"],
    tvd.ImageNet: ["train", "val"],
}

logger = get_logger(__name__)


def on_login_node() -> bool:
    # IDEA: Detect if we're on a login node somehow.
    return socket.getfqdn().endswith(".server.mila.quebec") and "SLURM_TMPDIR" not in os.environ


""" a map of the files for each dataset type, relative to `current_env.torchvision_dir`. """
dataset_files_paths: Dict[Type[VisionDataset], List[Path]] = {
    k: list(map(Path, v)) for k, v in dataset_files.items()
}


_IGNORED = ""


def make_dataset(dataset_type: Type[VD], root: str, *agrs, **kwargs) -> VD:
    # Check if the dataset is already downloaded in $current_env.fast_data_dir. If so, read and return it.
    # If not, check if the dataset is already stored somewhere in the cluster. If so, try to copy it
    # over to the fast directory. If that works, read the dataset from the fast directory.
    # If not, then download the dataset to the fast directory (if possible), and read it from there.
    if on_login_node():
        raise RuntimeError(f"Don't run this on a login node, you fool!")
    dataset = _try_load_fast(dataset_type, *agrs, **kwargs)
    if dataset is not None:
        logger.debug(f"Loaded the dataset from the fast directory.")
        return dataset
    dataset = _try_copy_from_slow(dataset_type, *agrs, **kwargs)
    if dataset is not None:
        logger.debug(f"Copied the dataset from slow to fast directory.")
        return dataset
    dataset = _try_download_fast(dataset_type, **kwargs)
    if dataset is not None:
        logger.debug(f"Dataset was downloaded to the fast directory.")
        return dataset
    return dataset_type(root, *args, **kwargs)


def _try_load_fast(dataset_type: Type[VD], **kwargs) -> Optional[VD]:
    assert "download" not in kwargs
    assert "root" not in kwargs
    try:
        return _create_dataset(
            dataset_type, root=current_env.fast_data_dir, download=False, **kwargs
        )
    except Exception as exc:
        logger.debug(f"Unable to load the dataset from the fast data directory: {exc}")
        return None


def _try_download_fast(dataset_type: Type[VD], download: bool = None, **kwargs) -> Optional[VD]:
    assert "root" not in kwargs
    try:
        return _create_dataset(
            dataset_type, root=current_env.fast_data_dir, download=True, **kwargs
        )
    except Exception as exc:
        logger.debug(f"Unable to download the dataset to the fast data directory: {exc}")
        return None


def _try_copy_from_slow(dataset_type: Type[VD], **kwargs) -> Optional[VD]:
    assert "download" not in kwargs
    assert "root" not in kwargs
    try:
        # Try to load the dataset from the torchvision directory.
        _ = _create_dataset(
            dataset_type, root=current_env.torchvision_dir, download=False, **kwargs
        )
    except Exception as exc:
        logger.debug(f"Unable to load the dataset from the torchvision directory: {exc}")
        return None
    try:
        _copy_files_to_fast_dir(dataset_type)
    except shutil.Error as err:
        logger.error(f"Unable to move files from data directory to fast directory: {err}")
        return None
    # We successfully copied files from the torchvision directory to the fast data directory.
    return _try_load_fast(dataset_type, **kwargs)


def _copy_files_to_fast_dir(dataset_type: Type[VisionDataset]) -> None:
    paths_to_copy = dataset_files_paths[dataset_type]

    for source_file in paths_to_copy:
        source_path = current_env.torchvision_dir / source_file
        destination_path = current_env.fast_data_dir / source_file
        logger.debug(f"Copying file from {source_path} -> {destination_path}")
        if source_path.is_dir():
            # Copy the folder over.
            # TODO: Check that this doesn't overwrite stuff, ignores files that are newer.
            # TODO: Test this out with symlinks, make sure it works.
            shutil.copytree(src=source_path, dst=destination_path, symlinks=False)
        elif not destination_path.exists():
            # Copy the file over.
            shutil.copy(src=source_path, dst=destination_path, follow_symlinks=False)


def _create_dataset(
    dataset_type: Type[VD], *args, root: Union[Path, str], download: bool = None, **kwargs
) -> VD:
    """ Creates the dataset using the arguments. If `download` is passed"""
    init_signature = inspect.signature(dataset_type.__init__)
    root_str = str(root)
    if "download" in init_signature.parameters.keys():
        return dataset_type(root=root_str, download=download, **kwargs)  # type: ignore
    else:
        return dataset_type(root=root_str, **kwargs)
