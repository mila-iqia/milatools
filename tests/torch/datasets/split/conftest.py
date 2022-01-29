import numpy

import pytest


@pytest.fixture
def N_CLASSES():
    return 10


@pytest.fixture
def N_POINTS():
    return 10000


@pytest.fixture
def N_TRAIN():
    return 8000


@pytest.fixture
def N_VALID():
    return 1000


@pytest.fixture
def N_TEST():
    return 1000


@pytest.fixture
def base_indices(N_POINTS):
    return list(range(N_POINTS))


@pytest.fixture
def rng():
    return numpy.random.RandomState(1)
