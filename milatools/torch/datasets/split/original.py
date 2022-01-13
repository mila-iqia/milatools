from milatools.torch.dataset.split.balanced_classes import Split


def split(datasets, data_size, seed, ratio, index, balanced):
    n_train = datasets.train_size
    n_valid = datasets.valid_size
    n_test = datasets.test_size
    n_points = len(datasets)

    assert n_points == n_train + n_valid + n_test

    return Split(
        train=range(n_train),
        valid=range(n_train, n_train + n_valid),
        test=range(n_train + n_valid, n_points),
    )
