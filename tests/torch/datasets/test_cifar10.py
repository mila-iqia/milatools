from milatools.torch.datasets.CIFAR10 import CIFAR10


def test_cifar():
    dataset = CIFAR10(download=True)

    # HPO, use valid set to select the best set of hyper-parameters
    train, valid, _ = dataset.splits(method="random")

    # Final training, use test set to select the best model
    train, _, test = dataset.splits(method="random", final=True)
