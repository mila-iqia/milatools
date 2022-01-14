"""Drop-in replacement for the `torchvision.datasets` package, optimized for Mila/CC clusters.

When running locally, there is no difference between using this package and torchvision.

When running on the Mila cluster, the only difference is that the `root` and `download` arguments
that are normally passed to the dataset class (e.g. `MNIST(root, download=True)`) could be ignored.
 
>>> # from torchvision.datasets import MNIST
>>> from milavision.datasets import MNIST
>>> dataset = MNIST("~/my/data/directory", download=True)
>>> dataset.root

>>>


The dataset 

might not be the pat

that you provide to the dataset class might not Dataset creation is altered slightly, de
"""
import functools

import torchvision.datasets as tvd

from milavision._utils import ClusterType

cluster_type = ClusterType.current()

# Import everything from torchvision, and then overwrite whatever we support.
from torchvision.datasets import *  # type: ignore
from torchvision.datasets import __all__

if cluster_type is ClusterType.LOCAL:
    # no change.
    pass

elif cluster_type is ClusterType.MILA:
    from milavision.envs.mila import create_dataset

    MNIST = functools.partial(create_dataset, tvd.MNIST)
    CIFAR10 = functools.partial(create_dataset, tvd.CIFAR10)
    CIFAR100 = functools.partial(create_dataset, tvd.CIFAR100)
    ImageNet = functools.partial(create_dataset, tvd.ImageNet)
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
