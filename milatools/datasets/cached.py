import os
import shutil


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

    def splits(self, final=False, train_transform=None, test_transform=None):
        """Returns the train, valid and test set for this dataset"""
        raise NotImplementedError()
