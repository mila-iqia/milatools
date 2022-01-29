import copy

import numpy

from milatools.torch.datasets.split.balanced_classes import (
    balanced_random_indices,
    Split,
)


def split_random_indices(rng, indices, n_train, n_valid, n_test, index):
    indices = numpy.array(copy.deepcopy(indices))
    rng.shuffle(indices)

    n_points = n_train + n_valid + n_test
    start = index * n_points
    if start + n_points > len(indices):
        raise ValueError(
            "Cannot have index `{}` for dataset of size `{}`".format(
                index, len(indices)
            )
        )
    train_indices = indices[start : start + n_train]
    valid_indices = indices[start + n_train : start + n_train + n_valid]
    test_indices = indices[
        start + n_train + n_valid : start + n_train + n_valid + n_test
    ]

    return Split(train=train_indices, valid=valid_indices, test=test_indices)


def split(datasets, data_size, seed, ratio, index, balanced):
    if index is None:
        index = 0

    return balanced_random_indices(
        method=split_random_indices,
        classes=datasets.classes,
        n_points=data_size,
        seed=seed,
        index=index,
    )
