from milatools.torch.checkpoint import Checkpoint


class Name:
    def __init__(self):
        self.a = "FirstName"

    def __str__(self):
        return f"Name {self.a}"


def test_checkpoint():

    name = Name()

    chk = Checkpoint(
        "/tmp/loc/",
        "my-experiment",
        obj=name,
    )

    chk.save()
    name.a = "SecondName"

    chk.load()
    assert name.a == "FirstName"
