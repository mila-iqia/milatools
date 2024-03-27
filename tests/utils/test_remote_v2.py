from __future__ import annotations

import asyncio
import logging
import subprocess
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

import milatools.utils.remote_v2
from milatools.cli.init_command import DRAC_CLUSTERS
from milatools.utils.remote_v2 import (
    ComputeNodeRemote,
    RemoteV2,
    UnsupportedPlatformError,
    control_socket_is_running,
    get_controlpath_for,
    is_already_logged_in,
    salloc,
    sbatch,
)
from tests.integration.conftest import skip_param_if_not_already_logged_in

from ..cli.common import (
    requires_ssh_to_localhost,
    xfails_on_windows,
)

pytestmark = [xfails_on_windows(raises=UnsupportedPlatformError, strict=True)]


class TestRemoteV2:
    @pytest.fixture(
        scope="session",
        params=[
            pytest.param("localhost", marks=requires_ssh_to_localhost),
            skip_param_if_not_already_logged_in("mila"),
            skip_param_if_not_already_logged_in("narval"),
            skip_param_if_not_already_logged_in("beluga"),
            skip_param_if_not_already_logged_in("cedar"),
            skip_param_if_not_already_logged_in("graham"),
            skip_param_if_not_already_logged_in("niagara"),
        ],
    )
    def hostname(self, request: pytest.FixtureRequest):
        hostname = request.param
        assert isinstance(hostname, str)
        return hostname

    @pytest.fixture(scope="class")
    def remote(self, hostname: str):
        return RemoteV2(hostname)

    @pytest.mark.parametrize("use_async_init", [False, True])
    @pytest.mark.asyncio
    async def test_init_with_controlpath(
        self, hostname: str, tmp_path: Path, use_async_init: bool
    ):
        # NOTE: Need to skip any cluster where 2FA might be enabled, because we're
        # specifying a different controlpath here so it would go through 2FA.
        if hostname in DRAC_CLUSTERS:
            pytest.skip(reason="2FA might be enabled on this cluster.")

        control_path = tmp_path / "socketfile"
        remote = (
            (await RemoteV2.connect(hostname, control_path=control_path))
            if use_async_init
            else RemoteV2(hostname, control_path=control_path)
        )
        assert control_path.exists()

        if hostname == "localhost":
            files = remote.get_output(f"ls {control_path.parent}").split()
            assert files == [control_path.name]

    @requires_ssh_to_localhost
    @pytest.mark.parametrize("use_async_init", [False, True])
    @pytest.mark.asyncio
    async def test_init_with_none_controlpath(
        self,
        hostname: str,
        use_async_init: bool,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        control_path = tmp_path / "socketfile"
        mock_get_controlpath_for = Mock(
            wraps=get_controlpath_for, return_value=control_path
        )

        monkeypatch.setattr(
            milatools.utils.remote_v2,
            get_controlpath_for.__name__,
            mock_get_controlpath_for,
        )
        remote = (
            (await RemoteV2.connect(hostname, control_path=None))
            if use_async_init
            else RemoteV2(hostname, control_path=None)
        )
        mock_get_controlpath_for.assert_called_once_with(hostname)
        assert control_path.exists()

        if hostname == "localhost":
            files = remote.get_output(f"ls {control_path.parent}").split()
            assert files == [control_path.name]

    @pytest.mark.parametrize(
        ("command", "expected_output", "expected_err"),
        [
            ("echo OK", "OK", ""),
            (
                "cat /does/not/exist",
                subprocess.CalledProcessError,
                "cat: /does/not/exist: No such file or directory",
            ),
        ],
    )
    @pytest.mark.parametrize("warn", [True, False])
    @pytest.mark.parametrize("display", [True, False])
    @pytest.mark.parametrize("hide", [True, False, "out", "err", "stdout", "stderr"])
    def test_run(
        self,
        remote: RemoteV2,
        command: str,
        expected_output: str | type[Exception],
        expected_err: str,
        hide: bool,
        warn: bool,
        display: bool,
        capsys: pytest.CaptureFixture,
        caplog: pytest.LogCaptureFixture,
    ):
        if isinstance(expected_output, type) and issubclass(expected_output, Exception):
            if not warn:
                # Should raise an exception of this type.
                with pytest.raises(expected_exception=expected_output):
                    _ = remote.run(command, display=display, hide=hide, warn=warn)
                # unreachable code here, so just pretend like it returns directly.
                return

            with caplog.at_level(logging.WARNING):
                result = remote.run(command, display=display, hide=hide, warn=warn)

            if hide is True:
                # Warnings not logged at all (because `warn=True` and `hide=True`).
                assert caplog.records == []
            else:
                assert len(caplog.records) == 1
                assert (
                    caplog.records[0].message.strip()
                    == f"Command {command!r} returned non-zero exit code 1: {expected_err}"
                )
            expected_output = ""
        else:
            result = remote.run(command, display=display, hide=hide, warn=warn)
            assert result.stdout.strip() == expected_output

        assert result.stdout.strip() == expected_output
        assert result.stderr.strip() == expected_err

        printed_output, printed_err = capsys.readouterr()
        assert isinstance(printed_output, str)
        assert isinstance(printed_err, str)

        assert (f"({remote.hostname}) $ {command}" in printed_output) == display

        if result.stdout:
            stdout_should_be_printed = hide not in [
                True,
                "out",
                "stdout",
            ]
            stdout_was_printed = result.stdout in printed_output
            assert stdout_was_printed == stdout_should_be_printed

        if result.stderr:
            error_should_be_printed = hide not in [
                True,
                "err",
                "stderr",
            ]
            error_was_printed = result.stderr in printed_err
            assert error_was_printed == error_should_be_printed, (
                result.stderr,
                printed_err,
            )

    @pytest.mark.asyncio
    async def test_run_async(self, remote: RemoteV2):
        commands = [f"sleep {i} && echo OK" for i in range(1, 3)]
        start_time = time.time()
        # Sequential time:
        sequential_results = [remote.get_output(command) for command in commands]
        sequential_time = time.time() - start_time

        start_time = time.time()
        parallel_results = await asyncio.gather(
            *(remote.get_output_async(command) for command in commands)
        )
        parallel_time = time.time() - start_time

        assert sequential_results == parallel_results
        assert parallel_time < sequential_time


# NOTE: The timeout here is a part of the test: if we are already connected, running the
# command should be fast, and if we aren't connected, this should be able to tell fast
# (in other words, it shouldn't wait for 2FA input or similar).
@pytest.mark.timeout(1, func_only=True)
@pytest.mark.parametrize("also_run_command_to_check", [False, True])
def test_is_already_logged_in(
    cluster: str, already_logged_in: bool, also_run_command_to_check: bool
):
    assert (
        is_already_logged_in(
            cluster, also_run_command_to_check=also_run_command_to_check
        )
        == already_logged_in
        == get_controlpath_for(cluster).exists()
    )


def test_controlsocket_is_running(cluster: str, already_logged_in: bool):
    control_path = get_controlpath_for(cluster)
    assert control_socket_is_running(cluster, control_path) == already_logged_in


# make it last a bit longer here so we don't confuse end of command/test with end of job.
@pytest.mark.asyncio
@pytest.mark.parametrize("allocation_flags", [{"time": "00:01:00"}], indirect=True)
async def test_salloc(login_node_v2: RemoteV2, allocation_flags: list[str]):
    compute_node = await salloc(login_node_v2, allocation_flags)
    assert isinstance(compute_node, ComputeNodeRemote)
    assert compute_node.hostname != login_node_v2.hostname

    # note: needs to be properly quoted so as not to evaluate the variable here!
    job_id = compute_node.get_output("echo $SLURM_JOB_ID")
    assert job_id.isdigit()
    assert compute_node.job_id == int(job_id)

    all_slurm_env_vars = {
        (split := line.split("="))[0]: split[1]
        for line in compute_node.get_output("env | grep SLURM").splitlines()
    }
    # NOTE: We don't yet have all the other SLURM env variables here yet because we're
    # only ssh-ing into the compute node.
    assert all_slurm_env_vars["SLURM_JOB_ID"] == str(compute_node.job_id)
    assert len(all_slurm_env_vars) > 1


def test_sbatch(login_node_v2: RemoteV2, allocation_flags: list[str]):
    compute_node = asyncio.run(sbatch(login_node_v2, allocation_flags))
    assert isinstance(compute_node, ComputeNodeRemote)

    assert compute_node.hostname != login_node_v2.hostname
    job_id = compute_node.get_output("echo $SLURM_JOB_ID")
    assert job_id.isdigit()
    assert compute_node.job_id == int(job_id)
    # Same here, only get SLURM_JOB_ID atm because we're ssh-ing into the node.
    all_slurm_env_vars = {
        (split := line.split("="))[0]: split[1]
        for line in compute_node.get_output("env | grep SLURM").splitlines()
    }
    assert all_slurm_env_vars["SLURM_JOB_ID"] == str(compute_node.job_id)
    assert len(all_slurm_env_vars) > 1
