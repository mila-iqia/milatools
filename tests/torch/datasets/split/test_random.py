import numpy

from olympus.datasets.split.split import split_random_indices


def test_split_deterministic(base_indices, N_TRAIN, N_VALID, N_TEST):
    indices = split_random_indices(
        numpy.random.RandomState(1), base_indices, N_TRAIN, N_VALID, N_TEST, index=0)
    new_indices = split_random_indices(
        numpy.random.RandomState(1), base_indices, N_TRAIN, N_VALID, N_TEST, index=0)

    for key in new_indices.keys():
        assert all(new_indices[key] == indices[key])

    new_indices = split_random_indices(
        numpy.random.RandomState(2), base_indices, N_TRAIN, N_VALID, N_TEST, index=0)

    for key in new_indices.keys():
        assert any(new_indices[key] != indices[key])


def test_split_size(base_indices, rng, N_TRAIN, N_VALID, N_TEST):
    indices = split_random_indices(rng, base_indices, N_TRAIN, N_VALID, N_TEST, index=0)
    assert indices['train'].shape[0] == N_TRAIN
    assert indices['valid'].shape[0] == N_VALID
    assert indices['test'].shape[0] == N_TEST

    assert numpy.unique(indices['train']).shape[0] == N_TRAIN
    assert numpy.unique(indices['valid']).shape[0] == N_VALID
    assert numpy.unique(indices['test']).shape[0] == N_TEST


def test_split_separation(base_indices, rng, N_TRAIN, N_VALID, N_TEST):
    # for all indexes
    # test that index 2 has first element after index 1
    indices = []
    for index_i in range(0, 9):
        rng.seed(1)  # Data should always be shuffled the same way.
        indices_i = split_random_indices(
            rng, base_indices, N_TRAIN // 10, N_VALID // 10, N_TEST // 10, index=index_i)

        assert set(indices_i['train']) & set(indices_i['valid']) == set()
        assert set(indices_i['train']) & set(indices_i['test']) == set()
        assert set(indices_i['valid']) & set(indices_i['test']) == set()

        set_i = set(sum(map(list, indices_i.values()), []))

        for index_j in range(index_i + 1, 10):
            rng.seed(1)  # Data should always be shuffled the same way.
            indices_j = split_random_indices(
                rng, base_indices, N_TRAIN // 10, N_VALID // 10, N_TEST // 10, index=index_j)

            set_j = set(sum(map(list, indices_j.values()), []))
            
            assert set_i & set_j == set(), (index_i, index_j)
