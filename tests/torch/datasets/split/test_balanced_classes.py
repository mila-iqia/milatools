import pytest

from milatools.torch.datasets.split.balanced_classes import balanced_random_indices


def _dummy_indices_method(rng, indices, n_train, n_valid, n_test):
    return dict(
        train=indices[:n_train],
        valid=indices[n_train : n_train + n_valid],
        test=indices[n_train + n_valid :],
    )


@pytest.fixture
def classes(N_POINTS, N_CLASSES):
    assert N_POINTS % N_CLASSES == 0
    data = [[] for i in range(N_CLASSES)]
    for i in range(N_POINTS):
        data[i % N_CLASSES].append(i)

    return data


def test_balanced_random_separation(classes, N_POINTS):
    indices = balanced_random_indices(_dummy_indices_method, classes, N_POINTS, seed=1)
    assert set(indices["train"]) & set(indices["valid"]) == set()
    assert set(indices["train"]) & set(indices["test"]) == set()
    assert set(indices["valid"]) & set(indices["test"]) == set()


def test_balanced_random_size(classes, N_TRAIN, N_VALID, N_TEST, N_POINTS):
    indices = balanced_random_indices(_dummy_indices_method, classes, N_POINTS, seed=1)
    assert len(indices["train"]) == N_TRAIN
    assert len(indices["valid"]) == N_VALID
    assert len(indices["test"]) == N_TEST


def test_balanced_random_balance(
    classes, N_TRAIN, N_VALID, N_TEST, N_POINTS, N_CLASSES
):
    indices = balanced_random_indices(_dummy_indices_method, classes, N_POINTS, seed=1)
    for class_indices in classes:
        assert len(set(indices["train"]) & set(class_indices)) == N_TRAIN // N_CLASSES
        assert len(set(indices["valid"]) & set(class_indices)) == N_VALID // N_CLASSES
        assert len(set(indices["test"]) & set(class_indices)) == N_TEST // N_CLASSES


def test_balanced_random_unbalance(classes, N_POINTS):
    with pytest.raises(AssertionError) as exc:
        balanced_random_indices(_dummy_indices_method, classes, N_POINTS + 1, seed=1)

    assert "n_points is not a multiple of number of classes" == str(exc.value)


def test_balanced_random_too_many_points(classes, N_POINTS, N_CLASSES):
    with pytest.raises(AssertionError) as exc:
        balanced_random_indices(
            _dummy_indices_method, classes, N_POINTS + N_CLASSES, seed=1
        )

    assert "n_points greater than nb of points available" == str(exc.value)


def test_balanced_random_deterministic(classes, N_POINTS):
    indices = balanced_random_indices(_dummy_indices_method, classes, N_POINTS, seed=1)
    new_indices = balanced_random_indices(
        _dummy_indices_method, classes, N_POINTS, seed=1
    )

    for key in new_indices.keys():
        assert all(new_indices[key] == indices[key])

    new_indices = balanced_random_indices(
        _dummy_indices_method, classes, N_POINTS, seed=2
    )

    for key in new_indices.keys():
        assert any(new_indices[key] != indices[key])


def test_balanced_random_shuffle(classes, N_POINTS):
    indices1 = balanced_random_indices(_dummy_indices_method, classes, N_POINTS, seed=1)
    indices2 = balanced_random_indices(_dummy_indices_method, classes, N_POINTS, seed=2)

    assert set(indices1["train"]) == set(indices2["train"])
    assert set(indices1["valid"]) == set(indices2["valid"])
    assert set(indices1["test"]) == set(indices2["test"])

    assert indices1["train"][0] != indices1["train"][1]
    assert indices1["valid"][0] != indices1["valid"][1]
    assert indices1["test"][0] != indices1["test"][1]
