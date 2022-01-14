""" IDEA: Have wrappers for datamodules of PyTorch Lightning as well. """
from typing import Protocol


class DataModule(Protocol):
    def train_dataloader(self, batch_size: int):
        pass

    def val_dataloader(self, batch_size: int):
        pass

    def test_dataloader(self, batch_size: int):
        pass
