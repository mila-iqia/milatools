import os

import pytest

from milatools.cli.remote import Remote

from .common import requires_s_flag

SERVER_NAME = os.environ.get("SERVER_NAME", "mila")


@pytest.fixture
def remote():
    return Remote(hostname=SERVER_NAME)


@requires_s_flag
def test_can_launch_srun(remote: Remote):
    output = remote.get_output(
        "srun --nodes=1 --ntasks=1 --cpus-per-task=1 --mem=1G --time=00:01:00 hostname"
    )
    assert False, output
