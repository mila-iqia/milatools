"""Drop-in replacement for the `torchvision.datasets` package, optimized for Mila/CC clusters.

When running locally, there is no difference between using this package and torchvision.

When running on the Mila cluster, the only difference is that the `root` and `download` arguments
that are normally passed to the dataset class (e.g. `MNIST(root, download=True)`) could be ignored.
 
>>> # from torchvision.datasets import MNIST
>>> from milavision.datasets import MNIST
>>> dataset = MNIST("~/my/data/directory", download=True)
>>> # dataset.root might not actually be "~/my/data/directory"!
"""
import functools
import typing

import torchvision.datasets as _tvd

# Import EVERYTHING from torchvision, and then overwrite whatever we support.
from torchvision.datasets import *  # type: ignore
from torchvision.datasets import __all__

from milavision._utils import ClusterType

for attribute, value in vars(_tvd).items():
    if attribute.startswith("__") and attribute.endswith("__"):
        continue
    if attribute not in __all__:
        """
        For all the attributes like `torchvision.datasets.vision`, `torchvision.datasets.utils`,
        or the modules like `torchvision.datasets.caltech`, etc, we copy over the value to the
        globals here.
        This makes it possible to do:
        
        ```python
        from milavision.datasets import caltech
        ```
        """
        globals()[attribute] = value

if typing.TYPE_CHECKING:
    # Import these here, so that when writing code, you get hints when doing something like:
    #
    # from milavision.datasets import caltech
    #
    # without the type-checker complaining.
    from torchvision.datasets import (
        caltech,
        celeba,
        cifar,
        cityscapes,
        coco,
        fakedata,
        flickr,
        folder,
        hmdb51,
        imagenet,
        kinetics,
        kitti,
        lsun,
        mnist,
        omniglot,
        phototour,
        places365,
        sbd,
        sbu,
        semeion,
        stl10,
        svhn,
        ucf101,
        usps,
        utils,
        video_utils,
        vision,
        voc,
        widerface,
    )


cluster_type = ClusterType.current()

if cluster_type is ClusterType.LOCAL:
    # no change.
    pass

elif cluster_type is ClusterType.MILA:
    from milavision.envs.mila import make_dataset

    MNIST = functools.partial(make_dataset, _tvd.MNIST)
    CIFAR10 = functools.partial(make_dataset, _tvd.CIFAR10)
    CIFAR100 = functools.partial(make_dataset, _tvd.CIFAR100)
    ImageNet = functools.partial(make_dataset, _tvd.ImageNet)
    Cityscapes functools.partial(make_dataset, _tvd.Cityscapes)
    # TODO: Find where the rest of these datasets are stored on the MILA cluster.
    # from tvd.lsun import LSUN, LSUNClass
    # from tvd.folder import ImageFolder, DatasetFolder
    # from tvd.coco import CocoCaptions, CocoDetection
    # from tvd.cifar import CIFAR10, CIFAR100
    # from tvd.stl10 import STL10
    # from tvd.mnist import MNIST, EMNIST, FashionMNIST, KMNIST, QMNIST
    # from tvd.svhn import SVHN
    # from tvd.phototour import PhotoTour
    # from tvd.fakedata import FakeData
    # from tvd.semeion import SEMEION
    # from tvd.omniglot import Omniglot
    # from tvd.sbu import SBU
    # from tvd.flickr import Flickr8k, Flickr30k
    # from tvd.voc import VOCSegmentation, VOCDetection
    # from tvd.cityscapes import Cityscapes
    # from tvd.imagenet import ImageNet
    # from tvd.caltech import Caltech101, Caltech256
    # from tvd.celeba import CelebA
    # from tvd.widerface import WIDERFace
    # from tvd.sbd import SBDataset
    # from tvd.vision import VisionDataset
    # from tvd.usps import USPS
    # from tvd.kinetics import Kinetics400
    # from tvd.hmdb51 import HMDB51
    # from tvd.ucf101 import UCF101
    # from tvd.places365 import Places365
    # from tvd.kitti import Kitti

else:
    raise NotImplementedError(cluster_type)
