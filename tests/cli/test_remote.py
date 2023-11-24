import pytest
from milatools.cli.remote import QueueIO, get_first_node_name


def test_QueueIO(file_regression):
    # TODO: This test doesn't do much.
    qio = QueueIO()
    strs = []

    i = 0

    qio.write("Begin")
    for _ in range(3):
        qio.write(f"\nline {i}")
        i += 1
    strs.append("".join(qio.readlines(lambda: True)))

    for _ in range(7):
        qio.write(f"\nline {i}")
        i += 1
    strs.append("".join(qio.readlines(lambda: True)))

    for _ in range(4):
        qio.write(f"\nline {i}")
        i += 1
    strs.append("".join(qio.readlines(lambda: True)))

    file_regression.check("\n=====".join(strs) + "\n^^^^^")


@pytest.mark.parametrize(
    ("node_string", "expected"),
    [
        ("cn-c001", "cn-c001"),
        ("cn-c[001-003]", "cn-c001"),
        ("cn-c[005,008]", "cn-c005"),
        ("cn-c001,rtx8", "cn-c001"),
    ],
)
def test_get_first_node_name(node_string: str, expected: str):
    assert get_first_node_name(node_string) == expected
