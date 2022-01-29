from collections import defaultdict

import torchvision.datasets as datasets
from torch.utils.data import ConcatDataset

from milatools.torch.datasets.cached import CachedDataset
from milatools.torch.datasets.transformed import Transformed
from milatools.torch.datasets.split import split


class ClassificationDataset:
    @property
    def classes(self):
        """Return the mapping between samples index and their class"""
        classes = defaultdict(list)

        for index, [_, y] in enumerate(self.dataset):
            classes[y].append(index)

        return [classes[i] for i in sorted(classes.keys())]


class CIFAR10(CachedDataset, ClassificationDataset):
    """The CIFAR-10 dataset (Canadian Institute For Advanced Research) is a collection of images
    that are commonly used to train machine learning and computer vision algorithms.
    It is one of the most widely used datasets for machine learning research.
    The CIFAR-10 dataset contains 60,000 32x32 color images in 10 different classes.
    More on `wikipedia <https://en.wikipedia.org/wiki/CIFAR-10>`_.
    The full specification can be found at `here <https://www.cs.toronto.edu/~kriz/cifar.html>`_.
    See also :class:`.CIFAR100`

    Attributes
    ----------
    input_shape: (3, 32, 32)
        Size of a sample stored in this dataset

    target_shape: (10,)
        There are 10 classes (airplane, automobile, bird, cat, deer, dog, frog, horse, ship, truck)

    train_size: 40000
        Size of the train set

    valid_size: 10000
        Size of the validation set

    test_size: 10000
        Size of the test set

    References
    ----------
    .. [1] Alex Krizhevsky, "Learning Multiple Layers of Features from Tiny Images", 2009.

    """

    train_size = 40000
    valid_size = 10000
    test_size = 10000

    @staticmethod
    def mila_path():
        """Path to where the dataset is stored inside the mila network"""
        return "/network/datasets/cifar10.var/cifar10_torchvision/"

    @staticmethod
    def build_dataset(*args, **kwargs):
        """Builds the expected dataset"""
        train_dataset = datasets.CIFAR10(*args, train=True, **kwargs)
        test_dataset = datasets.CIFAR10(*args, train=False, **kwargs)
        return ConcatDataset([train_dataset, test_dataset])

    def __init__(self, root=None, download=False):
        super(CIFAR10, self).__init__(root=root, download=download)
