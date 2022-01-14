from .._utils import VD
from typing import Type
from torchvision.datasets import *


def make_dataset(dataset_type: Type[VD], **kwargs) -> VD:
    return dataset_type(**kwargs)

