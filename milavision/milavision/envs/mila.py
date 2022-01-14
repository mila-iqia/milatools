from logging import getLogger as get_logger
import socket
from pathlib import Path
import os
import torchvision.datasets as tvd
from torchvision.datasets import VisionDataset
from typing import Dict, Optional, Type, List, Union
from .._utils import VD
import shutil
import inspect
from functools import partial

fast_data_dir: Path = Path(os.environ.get("SLURM_TMPDIR", ""))
torchvision_dir: Path = Path("/network/datasets/torchvision")

""" a map of the files for each dataset type, relative to the `torchvision_dir`. """
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


""" a map of the files for each dataset type, relative to `torchvision_dir`. """
dataset_files_paths: Dict[Type[VisionDataset], List[Path]] = {
    k: list(map(Path, v)) for k, v in dataset_files.items()
}


_IGNORED = ""


def make_dataset(
    dataset_type: Type[VD], root: str = _IGNORED, download: bool = False, **kwargs
) -> VD:
    # Check if the dataset is already downloaded in $SLURM_TMPDIR. If so, read and return it.
    # If not, check if the dataset is already stored somewhere in the cluster. If so, try to copy it
    # over to the fast directory. If that works, read the dataset from the fast directory.
    # If not, then download the dataset to the fast directory (if possible), and read it from there.
    if on_login_node():
        raise RuntimeError(f"Don't run this on a login node, you fool!")
    dataset = _try_load_fast(dataset_type, **kwargs)
    if dataset is not None:
        return dataset
    dataset = _try_copy_from_slow(dataset_type, **kwargs)
    if dataset is not None:
        return dataset
    return _download_fast(dataset_type, download=download, **kwargs)


def _try_load_fast(dataset_type: Type[VD], **kwargs) -> Optional[VD]:
    assert "download" not in kwargs
    assert "root" not in kwargs
    try:
        return create_dataset(dataset_type, root=fast_data_dir, download=False, **kwargs)
    except Exception as exc:
        logger.debug(f"Unable to load the dataset from the fast data directory: {exc}")
        return None


def _download_fast(dataset_type: Type[VD], download: bool = None, **kwargs) -> VD:
    assert "root" not in kwargs
    return create_dataset(dataset_type, root=fast_data_dir, download=download, **kwargs)


def _try_copy_from_slow(dataset_type: Type[VD], **kwargs) -> Optional[VD]:
    assert "download" not in kwargs
    assert "root" not in kwargs
    try:
        # Try to load the dataset from the torchvision directory.
        _ = create_dataset(dataset_type, root=torchvision_dir, download=False, **kwargs)
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
    for source_path in paths_to_copy:
        destination_path = fast_data_dir / source_path
        if source_path.is_dir():
            # Copy the folder over.
            # TODO: Check that this doesn't overwrite stuff, ignores files that are newer.
            # TODO: Test this out with symlinks, make sure it works.
            shutil.copytree(
                src=source_path, dst=destination_path, symlinks=False, dirs_exist_ok=True,
            )
        elif not destination_path.exists():
            # Copy the file over.
            shutil.copy(src=source_path, dst=destination_path, follow_symlinks=False)


def create_dataset(
    dataset_type: Type[VD], *args, root: Union[Path, str], download: bool = None, **kwargs
) -> VD:
    """ Creates the dataset using the arguments. If `download` is passed"""
    init_signature = inspect.signature(dataset_type.__init__)
    root_str = str(root)
    if "download" in init_signature.parameters.keys():
        return dataset_type(root=root_str, download=download, **kwargs)  # type: ignore
    else:
        return dataset_type(root=root_str, **kwargs)

