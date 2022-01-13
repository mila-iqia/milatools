from dataclasses import dataclass

from milatools.utils import fetch_factories

import numpy


sampling_methods = fetch_factories(
    "milatools.torch.dataset.split", __file__, function_name="split"
)


def split(datasets, method, *args):
    """Split the dataset in 3 mutually exclusive sets

    References
    ----------

    .. [1] Bouthillier, X., Delaunay, P., Bronzi, M., Trofimov, A., Nichyporuk, B., Szeto, J., ... & Vincent, P. (2021).
           Accounting for variance in machine learning benchmarks. Proceedings of Machine Learning and Systems, 3.

    .. [2] Gorman, K., & Bedrick, S. (2019, July).
           We need to talk about standard splits. In Proceedings of the 57th annual meeting of the association for computational linguistics (pp. 2786-2791).

    """

    split_method = sampling_methods.get(method)

    if split_method is not None:
        return split_method(*args)

    raise NotImplementedError(f"Split method {method} is not implemented")
