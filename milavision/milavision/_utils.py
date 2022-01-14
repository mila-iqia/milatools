from torch.utils.data import Dataset
import socket
import os
import enum
from pathlib import Path
from typing import TypeVar

from torchvision.datasets.vision import VisionDataset

try:
    pass
except ImportError:
    pass

D = TypeVar("D", bound=Dataset)
VD = TypeVar("VD", bound=VisionDataset)


class ClusterType(enum.Enum):
    """ Enum of the different clusters available. """

    LOCAL = enum.auto()
    MILA = enum.auto()
    CEDAR = enum.auto()
    BELUGA = enum.auto()
    GRAHAM = enum.auto()

    @classmethod
    def current(cls) -> "ClusterType":
        if socket.getfqdn().endswith(".server.mila.quebec") and "SLURM_TMPDIR" in os.environ:
            return cls.MILA
        # TODO: Add checks for the other clusters.
        return cls.LOCAL

    @property
    def torchvision_dir(self) -> Path:
        if self is ClusterType.LOCAL:
            return Path("data")
        if self is ClusterType.MILA:
            return Path("/network/datasets/torchvision")
        raise NotImplementedError(self)  # todo

    @property
    def fast_data_dir(self) -> Path:
        """ Returns the 'fast' directory where datasets are stored for quick read/writtes. """
        if self is ClusterType.LOCAL:
            return Path("data")
        if self is ClusterType.MILA:
            return Path(os.environ["SLURM_TMPDIR"])
        raise NotImplementedError(self)  # todo
