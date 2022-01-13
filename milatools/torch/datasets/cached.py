import os
import shutil
import tempfile

from milatools.torch.datasets.split import split
from milatools.torch.datasets.transformed import Transformed

from torch.utils.data import ConcatDataset, Dataset, Subset


def get_temp_folder():
    return os.environ.get("SLURM_TMPDIR", "/tmp/")


class CachedDataset:
    """Simple dataset that moves the dataset from a shared network location to a fast local location"""

    WAS_COPIED = False
    train_size = None
    valid_size = None
    test_size = None

    def __init__(self, root=None, *args, download=False, **kwargs):
        # NB: for HPO (multiple unrelated workers on the same machine)
        # we will need a filelock to avoid workers all downloading the same dataset

        # TODO: only dist.has_dataset_authority() can download
        if os.path.exists(self.mila_path()):
            download = False

        # Create a deterministic location so other workers on the same machine
        # can access the data
        if root is None:
            root = os.path.join(get_temp_folder(), "milatools", type(self).__name__)

        # TODO: only dist.has_dataset_authority() can copy
        if os.path.exists(self.mila_path()) and not type(self).WAS_COPIED:
            shutil.copytree(self.mila_path(), root, dirs_exist_ok=True)
            type(self).WAS_COPIED = True

        self.dataset = self.build_dataset(root, *args, download=download, **kwargs)

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

        splits = split(self, method, len(self.dataset))

        trainset = Subset(self, splits.train)
        validset = Subset(self, splits.valid)
        testset = Subset(self, splits.test)

        if final:
            trainset = ConcatDataset([trainset, validset])
            # Valid set get merged to get a bigger trainset when HPO is done
            validset = None
        else:
            # Not allowed to use testset during HPO
            testset = None

        return (
            Transformed(trainset, train_transform) if trainset else None,
            Transformed(validset, inference_transform) if validset else None,
            Transformed(testset, inference_transform) if testset else None,
        )
