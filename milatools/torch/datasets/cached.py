import os
import shutil

from milatools.torch.datasets.split import split
from milatools.torch.datasets.transformed import Transformed

from torch.utils.data import ConcatDataset, Dataset, Subset


class CachedDataset:
    """Simple dataset that moves the dataset from a shared network location to a fast local location"""

    WAS_COPIED = False
    train_size = None
    valid_size = None
    test_size = None

    def __init__(self, root, download=False):

        if os.path.exists(self.mila_path()):
            download = False

        if not CachedDataset.WAS_COPIED:
            shutil.copytree(self.mila_path(), root, dirs_exist_ok=True)
            CachedDataset.WAS_COPIED = True

        self.dataset = self.build_dataset(root, download=download)

    @staticmethod
    def dataset_class():
        """Original pytorch dataset class"""
        raise NotImplementedError()

    @staticmethod
    def mila_path():
        """Path to where the dataset is stored inside the mila network"""
        raise NotImplementedError()

    @staticmethod
    def build_dataset(*args, **kwargs):
        """Builds the expected dataset"""
        return CachedDataset.dataset_class()(*args, **kwargs)

    def __getitem__(self, idx):
        """Fetch a sample inside the dataset"""
        return self.dataset[idx]

    def __len__(self):
        """Returns the size of the dataset"""
        return len(self.dataset)

    def splits(
        self,
        method="original",
        final=False,
        train_transform=None,
        inference_transform=None,
    ):
        """Split the dataset in 3 train, valid, test.
        When the hyperparameter optimization is done, final is set to True and
        train and valid are merged together.

        Parameters
        ----------

        method: str
            split method to use, defaults to original which use the standard dataset split

        final: bool
            When set to true merge the test and validation set together

        train_transform:
            Per sample transformation applied to your trainning set

        inference_transform:
            Per sample transformation applied to both the valid test test set.

        Notes
        -----

        Randomizing the dataset splits is important to be able to effectively benchmark
        your model

        """

        splits = split(self, method)

        trainset = Subset(self, splits.train)
        validset = Subset(self, splits.valid)
        testset = Subset(self, splits.test)

        if final:
            trainset = ConcatDataset([trainset, validset])
            # Valid set get merged to get a bigger trainset
            # when HPO is done
            validset = None
        else:
            # Not allowed to use testset during HPO
            testset = None

        return (
            Transformed(trainset, train_transform) if trainset else None,
            Transformed(validset, inference_transform) if validset else None,
            Transformed(testset, inference_transform) if testset else None,
        )
