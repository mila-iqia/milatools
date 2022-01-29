from collections import OrderedDict
from dataclasses import dataclass

import numpy


def balanced_random_indices(method, classes, n_points, seed, split_ratio=0.1, **kwargs):
    assert (
        n_points % len(classes) == 0
    ), "n_points is not a multiple of number of classes"

    n_points_per_class = n_points // len(classes)
    assert n_points_per_class <= len(
        classes[0]
    ), "n_points greater than nb of points available"

    n_test_per_class = int(numpy.ceil(n_points_per_class * split_ratio))
    n_valid_per_class = n_test_per_class
    n_train_per_class = n_points_per_class - n_test_per_class - n_valid_per_class
    assert (
        n_train_per_class + n_valid_per_class + n_test_per_class == n_points_per_class
    )

    rng = numpy.random.RandomState(int(seed))

    sampled_indices = Split(train=[], valid=[], test=[])

    for indices in classes:
        class_sampled_indices = method(
            rng,
            indices,
            n_train_per_class,
            n_valid_per_class,
            n_test_per_class,
            **kwargs
        )

        for set_name in sampled_indices.keys():
            sampled_indices[set_name].extend(class_sampled_indices[set_name])

    # Make sure they are not grouped by class
    for set_name in sampled_indices.keys():
        rng.shuffle(sampled_indices[set_name])
        sampled_indices[set_name] = numpy.array(sampled_indices[set_name])

    return sampled_indices


@dataclass
class Split:
    """Returns 3 Splits of the main data set. The splits are indices of the samples inside the data set"""

    train: numpy.array
    valid: numpy.array
    test: numpy.array

    def items(self):
        return ("train", self.train), ("valid", self.valid), ("test", self.test)

    def __getitem__(self, item):
        return getattr(self, item)

    def __setitem__(self, key, value):
        return setattr(self, key, value)

    def keys(self):
        return "train", "valid", "test"

    def values(self):
        return self.train, self.valid, self.test
