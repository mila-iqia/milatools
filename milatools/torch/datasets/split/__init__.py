from dataclasses import dataclass

from milatools.utils import fetch_factories

import numpy


sampling_methods = fetch_factories(
    "milatools.torch.datasets.split", __file__, function_name="split"
)


def split(
    datasets, method, data_size=None, seed=0, ratio=0.1, index=None, balanced=False
):
    """Split the dataset in 3 mutually exclusive sets

    Attributes
    ----------
    method: str
        Which algorithm to use to generate the splits

    seed: int
        Seed of the PRNG, when the split use split to generate the splits

    ratio: float
        Split Ratio for the test and validation test (default: 0.1 i.e 10%)

    data_size: int
        Specify the number of points. It defaults to the full size of the dataset.

    index: int
        If data size is small enough, multiple splits of the same data set can be extracted.
        index specifies which of those splits is fetched

    balanced: bool
        If true, the splits will keep the classes balanced.


    References
    ----------

    .. [1] Bouthillier, X., Delaunay, P., Bronzi, M., Trofimov, A., Nichyporuk, B., Szeto, J., ... & Vincent, P. (2021).
           Accounting for variance in machine learning benchmarks. Proceedings of Machine Learning and Systems, 3.

    .. [2] Gorman, K., & Bedrick, S. (2019, July).
           We need to talk about standard splits. In Proceedings of the 57th annual meeting of the association for computational linguistics (pp. 2786-2791).

    """
    if data_size is None:
        data_size = len(datasets)

    assert data_size <= len(datasets)
    split_method = sampling_methods.get(method)

    if split_method is not None:
        return split_method(datasets, data_size, seed, ratio, index, balanced)

    raise NotImplementedError(f"Split method {method} is not implemented")
