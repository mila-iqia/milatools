from milatools.cli.remote import QueueIO, get_first_node_name


def test_QueueIO(file_regression):
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


def test_get_first_node_name(file_regression):
    file_regression.check(
        "\n".join(
            (
                get_first_node_name("cn-c001"),
                get_first_node_name("cn-c[001-003]"),
                get_first_node_name("cn-c[005,008]"),
                get_first_node_name("cn-c001,rtx8"),
            )
        )
    )
