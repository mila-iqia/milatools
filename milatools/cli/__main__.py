import os
import re
import socket
import subprocess
import time
import webbrowser
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlencode

import questionary as qn
from coleo import Option, auto_cli, default, tooled

from ..version import version as mversion
from .profile import ensure_program, setup_profile
from .utils import (
    Local,
    Remote,
    SlurmRemote,
    SSHConfig,
    T,
    randname,
    with_control_file,
    yn,
)


def main():
    """Entry point for milatools."""
    on_mila = socket.getfqdn().endswith(".server.mila.quebec")
    if on_mila:
        exit(
            "Error: 'mila ...' should be run on your local machine and not on the Mila cluster"
        )
    auto_cli(milatools)


class milatools:
    """Tools to connect to and interact with the Mila cluster.

    Cluster documentation: https://docs.mila.quebec/
    """

    def __main__():
        # This path is triggered when no command is passed

        # Milatools version
        # [alias: -v]
        version: Option & bool = default(False)

        if version:
            print(f"milatools v{mversion}")

    def docs():
        """Open the Mila cluster documentation."""
        # Search terms
        # [remainder]
        search: Option = default([])
        url = "https://docs.mila.quebec"
        if search:
            terms = "+".join(search)
            url = f"{url}/search.html?q={terms}"
        print(f"Opening the docs: {url}")
        webbrowser.open(url)

    def intranet():
        """Open the Mila intranet in a browser."""
        # Search terms
        # [remainder]
        search: Option = default([])
        if search:
            terms = "+".join(search)
            url = f"https://sites.google.com/search/mila.quebec/mila-intranet?query={terms}&scope=site&showTabs=false"
        else:
            url = "https://intranet.mila.quebec"
        print(f"Opening the intranet: {url}")
        webbrowser.open(url)

    def init():
        """Set up your configuration and credentials."""

        #############################
        # Step 1: SSH Configuration #
        #############################

        print("Checking ssh config")

        sshpath = os.path.expanduser("~/.ssh")
        cfgpath = os.path.join(sshpath, "config")
        if not os.path.exists(cfgpath):
            if yn("There is no ~/.ssh/config file. Create one?"):
                if not os.path.exists(sshpath):
                    os.makedirs(sshpath, mode=0o700, exist_ok=True)
                open(cfgpath, "w").close()
                os.chmod(cfgpath, 0o600)
                print(f"Created {cfgpath}")
            else:
                exit("No ssh configuration file was found.")

        c = SSHConfig(cfgpath)
        changes = False

        # Check for a mila entry in ssh config

        if "mila" not in c.hosts():
            if yn("There is no 'mila' entry in ~/.ssh/config. Create one?"):
                username = ""
                while not username:
                    username = input(T.bold("What is your username?\n> "))
                c.add(
                    "mila",
                    HostName="login.server.mila.quebec",
                    User=username,
                    PreferredAuthentications="publickey,keyboard-interactive",
                    Port="2222",
                    ServerAliveInterval="120",
                    ServerAliveCountMax="5",
                )
                if not c.confirm("mila"):
                    exit("Did not change ssh config")
            else:
                exit("Did not change ssh config")
            changes = True

        # Check for *.server.mila.quebec in ssh config, to connect to compute nodes

        cnode_pattern = "*.server.mila.quebec !*login.server.mila.quebec"

        if "*.server.mila.quebec" in c.hosts():
            if yn(
                "The '*.server.mila.quebec' entry in ~/.ssh/config is too general and should exclude login.server.mila.quebec. Fix this?"
            ):
                c.rename("*.server.mila.quebec", cnode_pattern)
                changes = True

        if cnode_pattern not in c.hosts():
            if yn(
                "There is no '*.server.mila.quebec' entry in ~/.ssh/config. Create one?"
            ):
                username = c.host("mila")["user"]
                c.add(
                    cnode_pattern,
                    HostName="%h",
                    User=username,
                    ProxyJump="mila",
                )
                if not c.confirm(cnode_pattern):
                    exit("Did not change ssh config")
            else:
                exit("Did not change ssh config")
            changes = True

        if changes:
            c.save()
            print("Wrote ~/.ssh/config")

        print("# OK")

        #############################
        # Step 2: Passwordless auth #
        #############################

        print("Checking passwordless authentication")

        here = Local()

        # Check that there is an id file

        sshdir = os.path.expanduser("~/.ssh")
        if not any(
            entry.startswith("id") and entry.endswith(".pub")
            for entry in os.listdir(sshdir)
        ):
            if yn("You have no public keys. Generate one?"):
                here.run("ssh-keygen")
            else:
                exit("No public keys.")

        # Check that it is possible to connect using the key

        if not here.check_passwordless("mila"):
            if yn(
                "Your public key does not appear be registered on the cluster. Register it?"
            ):
                here.run("ssh-copy-id", "mila")
                if not here.check_passwordless("mila"):
                    exit("ssh-copy-id appears to have failed")
            else:
                exit("No passwordless login.")

        #####################################
        # Step 3: Set up keys on login node #
        #####################################

        print("Checking connection to compute nodes")

        remote = Remote("mila")
        try:
            pubkeys = remote.get_lines("ls -t ~/.ssh/id*.pub")
            print("# OK")
        except subprocess.CalledProcessError:
            print("# MISSING")
            if yn("You have no public keys on the login node. Generate them?"):
                # print("(Note: You can just press Enter 3x to accept the defaults)")
                # _, keyfile = remote.extract("ssh-keygen", pattern="Your public key has been saved in ([^ ]+)", wait=True)
                private_file = "~/.ssh/id_rsa"
                remote.run(f'ssh-keygen -q -t rsa -N "" -f {private_file}')
                pubkeys = [f"{private_file}.pub"]
            else:
                exit("Cannot proceed because there is no public key")

        common = remote.with_bash().get_output(
            "comm -12 <(sort ~/.ssh/authorized_keys) <(sort ~/.ssh/*.pub)"
        )
        if common:
            print("# OK")
        else:
            print("# MISSING")
            if yn(
                "To connect to a compute node from a login node you need one id_*.pub to be in authorized_keys. Do it?"
            ):
                pubkey = pubkeys[0]
                remote.run(f"cat {pubkey} >> ~/.ssh/authorized_keys")

    def forward():
        """Forward a port on a compute node to your local machine."""

        # node:port to forward
        # [positional]
        remote: Option

        node, remote_port = remote.split(":")
        try:
            remote_port = int(remote_port)
        except ValueError:
            pass

        # String to append after the URL
        page: Option = default(None)

        local_proc = _forward(
            local=Local(),
            node=f"{node}.server.mila.quebec",
            to_forward=remote_port,
            page=page,
        )

        try:
            local_proc.wait()
        except KeyboardInterrupt:
            exit("Terminated by user.")
        finally:
            local_proc.kill()

    def code():
        """Open a remote VSCode session on a compute node."""
        # Path to open on the remote machine
        # [positional]
        path: Option

        remote = Remote("mila")
        here = Local()

        cnode = _find_allocation(remote)
        node_name, proc = cnode.ensure_allocation()

        if not path.startswith("/"):
            # Get $HOME because we have to give the full path to code
            home = remote.home()
            path = "/".join([home, path])

        time.sleep(1)
        if "." not in node_name and not node_name.endswith(".server.mila.quebec"):
            node_name = f"{node_name}.server.mila.quebec"
        try:
            here.run(
                "code",
                "-nw",
                "--remote",
                f"ssh-remote+{node_name}",
                path,
            )
        except KeyboardInterrupt:
            pass
        if proc is not None:
            proc.kill()
        print(f"Ended session on '{node_name}'")

    class serve:
        """Start services on compute nodes and forward them to your local machine."""

        def connect():
            """Reconnect to a persistent server."""

            remote = Remote("mila")
            _, info = _get_server_info_command(remote)

            local_proc = _forward(
                local=Local(),
                node=f"{info['node_name']}.server.mila.quebec",
                to_forward=info["to_forward"],
                options={"token": info.get("token", None)},
                preferred_port=info["local_port"],
                through_login=info["host"] == "0.0.0.0",
            )

            try:
                local_proc.wait()
            except KeyboardInterrupt:
                exit("Terminated by user.")
            finally:
                local_proc.kill()

        def kill():
            """Kill a persistent server."""

            # Server identifier output by the original mila serve command
            # [positional: ?]
            identifier: Option = default(None)

            # Kill all servers
            all: Option & bool = default(False)

            remote = Remote("mila")

            if all:
                for identifier in remote.get_lines("ls .milatools/control", hide=True):
                    info = _get_server_info(remote, identifier, hide=True)
                    if "jobid" in info:
                        remote.run(f"scancel {info['jobid']}")
                    remote.run(f"rm .milatools/control/{identifier}")

            elif identifier is None:
                exit("Please give the name of the server to kill")

            else:
                info = _get_server_info(remote, identifier)

                remote.run(f"scancel {info['jobid']}")
                remote.run(f"rm .milatools/control/{identifier}")

        def list():
            """List active servers."""

            # Purge dead or invalid servers
            purge: Option & bool = default(False)

            remote = Remote("mila")

            to_purge = []

            remote.run("mkdir -p ~/.milatools/control", hide=True)

            for identifier in remote.get_lines("ls .milatools/control", hide=True):
                info = _get_server_info(remote, identifier, hide=True)
                jobid = info.get("jobid", None)
                status = remote.get_output(
                    f"squeue -j {jobid} -ho %T", hide=True, warn=True
                )
                program = info.pop("program", "???")
                if status == "RUNNING":
                    necessary_keys = {"node_name", "to_forward"}
                    if any(k not in info for k in necessary_keys):
                        qn.print(
                            f"{identifier} ({program}, MISSING INFO)", style="bold red"
                        )
                        to_purge.append((identifier, jobid))
                    else:
                        qn.print(f"{identifier} ({program})", style="bold yellow")
                else:
                    qn.print(f"{identifier} ({program}, DEAD)", style="bold red")
                    to_purge.append((identifier, None))
                for k, v in info.items():
                    print(f"    {k:20} : {v}")

            if purge:
                for identifier, jobid in to_purge:
                    if jobid is not None:
                        remote.run(f"scancel {jobid}")
                    remote.run(f"rm .milatools/control/{identifier}")

        def lab():
            """Start a Jupyterlab server."""

            # Path to open on the remote machine
            # [positional: ?]
            path: Option = default(None)

            path = path or "~"
            if path.endswith(".ipynb"):
                exit("Only directories can be given to the mila serve lab command")

            _standard_server(
                path,
                program="jupyter-lab",
                installers={
                    "conda": "conda install -y jupyterlab",
                    "pip": "pip install jupyterlab",
                },
                command="jupyter lab --sock {sock}",
                # command="jupyter lab --ip {host} --port 0",
                token_pattern=r"\?token=([a-f0-9]+)",
            )

        def notebook():
            """Start a Jupyter Notebook server."""

            # Path to open on the remote machine
            # [positional: ?]
            path: Option = default(None)

            path = path or "~"
            if path.endswith(".ipynb"):
                exit("Only directories can be given to the mila serve notebook command")

            _standard_server(
                path,
                program="jupyter-notebook",
                installers={
                    "conda": "conda install -y jupyter",
                    "pip": "pip install jupyter",
                },
                command="jupyter notebook --sock {sock}",
                # command="jupyter notebook --ip {host} --port 0",
                token_pattern=r"\?token=([a-f0-9]+)",
            )

        def tensorboard():
            """Start a Tensorboard server."""

            # Path to the experiment logs
            # [positional]
            logdir: Option

            _standard_server(
                logdir,
                program="tensorboard",
                installers={
                    "conda": "conda install -y tensorboard",
                    "pip": "pip install tensorboard",
                },
                command="tensorboard --logdir {path} --host {host} --port 0",
                port_pattern="TensorBoard [^ ]+ at http://[^:]+:([0-9]+)/",
            )

        def mlflow():
            """Start an MLFlow server."""

            # Path to the experiment logs
            # [positional]
            logdir: Option

            _standard_server(
                logdir,
                program="mlflow",
                installers={
                    "pip": "pip install mlflow",
                },
                command="mlflow ui --backend-store-uri {path} --host {host} --port 0",
                port_pattern="Listening at: http://[^:]+:([0-9]+)",
            )

        def aim():
            """Start an AIM server."""

            # Path to the experiment logs
            # [positional]
            logdir: Option

            _standard_server(
                logdir,
                program="aim",
                installers={
                    "pip": "pip install aim",
                },
                command="aim up --repo {path} --host {host} --port 0",
                port_pattern=f"Open http://[^:]+:([0-9]+)",
            )


def _get_server_info(remote, identifier, hide=False):
    text = remote.get_output(f"cat .milatools/control/{identifier}", hide=hide)
    info = dict(line.split(" = ") for line in text.split("\n") if line)
    return info


@tooled
def _get_server_info_command(remote):
    # Server identifier output by the original mila serve command
    # [positional]
    identifier: Option

    return identifier, _get_server_info(remote, identifier)


@tooled
def _standard_server(
    path,
    program,
    installers,
    command,
    port_pattern=None,
    token_pattern=None,
):

    # Name of the profile to use
    profile: Option = default(None)

    # Whether the server should persist or not
    persist: Option & bool = default(False)

    # Name of the persistent server
    name: Option = default(None)

    # Make the server visible from the login node (other users will be able to connect)
    # share: Option & bool = default(False)
    # Temporarily disabled
    share = False

    if name is not None:
        persist = True
    elif persist:
        name = program

    remote = Remote("mila")

    path = path or "~"

    with ExitStack() as stack:
        if persist:
            cf = stack.enter_context(with_control_file(remote, name=name))
        else:
            cf = None

        if profile:
            prof = f"~/.milatools/profiles/{profile}.bash"
        else:
            prof = setup_profile(remote, path)

        qn.print(f"Using profile: {prof}")
        cat_result = remote.run(f"cat {prof}", hide=True, warn=True)
        if cat_result.ok:
            qn.print(f"=" * 50)
            qn.print(cat_result.stdout.rstrip())
            qn.print(f"=" * 50)
        else:
            exit(f"Could not find or load profile: {prof}")

        premote = remote.with_profile(prof)

        if not ensure_program(
            remote=premote,
            program=program,
            installers=installers,
        ):
            exit(f"Exit: {program} is not installed.")

        cnode = _find_allocation(remote)

        patterns = {
            "node_name": "#### ([A-Za-z0-9_-]+)",
        }

        if port_pattern:
            patterns["port"] = port_pattern
        elif share:
            exit(
                "Server cannot be shared because it is serving over a Unix domain socket"
            )
        else:
            remote.run("mkdir -p ~/.milatools/sockets", hide=True)

        if share:
            host = "0.0.0.0"
        else:
            host = "localhost"

        sock_name = name or randname()
        command = command.format(
            path=path,
            sock=f"~/.milatools/sockets/{sock_name}.sock",
            host=host,
        )

        if token_pattern:
            patterns["token"] = token_pattern

        if persist:
            cnode = cnode.persist()

        proc, results = (
            cnode.with_profile(prof)
            .with_precommand("echo '####' $(hostname)")
            .extract(
                command,
                patterns=patterns,
            )
        )
        node_name = results["node_name"]

        if port_pattern:
            to_forward = int(results["port"])
        else:
            to_forward = f"{remote.home()}/.milatools/sockets/{sock_name}.sock"

        if cf is not None:
            remote.simple_run(f"echo program = {program} >> {cf}")
            remote.simple_run(f"echo node_name = {results['node_name']} >> {cf}")
            remote.simple_run(f"echo host = {host} >> {cf}")
            remote.simple_run(f"echo to_forward = {to_forward} >> {cf}")
            if token_pattern:
                remote.simple_run(f"echo token = {results['token']} >> {cf}")

    if token_pattern:
        options = {"token": results["token"]}
    else:
        options = {}

    local_proc = _forward(
        local=Local(),
        node=f"{node_name}.server.mila.quebec",
        to_forward=to_forward,
        options=options,
    )

    if cf is not None:
        remote.simple_run(f"echo local_port = {local_proc.local_port} >> {cf}")

    try:
        local_proc.wait()
    except KeyboardInterrupt:
        qn.print("Terminated by user.")
        if cf is not None:
            name = Path(cf).name
            qn.print("To reconnect to this server, use the command:")
            qn.print(f"  mila serve connect {name}", style="bold yellow")
            qn.print("To kill this server, use the command:")
            qn.print(f"  mila serve kill {name}", style="bold red")
    finally:
        local_proc.kill()
        proc.kill()


@tooled
def _find_allocation(remote):
    # Node to connect to
    node: Option = default(None)

    # Job ID to connect to
    job: Option = default(None)

    # Extra options to pass to slurm
    # [nargs: --]
    alloc: Option = default([])

    if (node is not None) + (job is not None) + bool(alloc) > 1:
        exit("ERROR: --node, --job and --alloc are mutually exclusive")

    if node is not None:
        node_name = f"{node}.server.mila.quebec"
        return Remote(node_name)

    elif job is not None:
        node_name = remote.get_output(f"squeue --jobs {job} -ho %N")
        return Remote(node_name)

    else:
        return SlurmRemote(
            connection=remote.connection,
            alloc=alloc,
        )


@tooled
def _forward(
    local,
    node,
    to_forward,
    page=None,
    options={},
    preferred_port=None,
    through_login=False,
):

    # Port to open on the local machine
    port: Option = default(preferred_port)

    if port is None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Find a free local port by binding to port 0
        sock.bind(("localhost", 0))
        _, port = sock.getsockname()
        # Close it for ssh -L. It is *unlikely* it will not be available.
        sock.close()

    if isinstance(to_forward, int) or re.match("[0-9]+", to_forward):
        if through_login:
            to_forward = f"{node}:{to_forward}"
            args = [f"localhost:{port}:{to_forward}", "mila"]
        else:
            to_forward = f"localhost:{to_forward}"
            args = [f"localhost:{port}:{to_forward}", node]
    else:
        args = [f"localhost:{port}:{to_forward}", node]

    proc = local.popen(
        "ssh",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "StrictHostKeyChecking=no",
        "-nNL",
        *args,
    )

    url = f"http://localhost:{port}"
    if page is not None:
        if not page.startswith("/"):
            page = f"/{page}"
        url += page

    options = {k: v for k, v in options.items() if v is not None}
    if options:
        url += f"?{urlencode(options)}"

    qn.print("Waiting for connection to be active...")
    nsecs = 10
    period = 0.2
    for _ in range(int(nsecs / period)):
        time.sleep(period)
        try:
            # This feels stupid, there's probably a better way
            local.silent_get("nc", "-z", "localhost", str(port))
        except subprocess.CalledProcessError as exc:
            continue
        except Exception as exc:
            break
        break

    qn.print(
        "Starting browser. You might need to refresh the page.",
        style="bold",
    )
    webbrowser.open(url)
    proc.local_port = port
    return proc


if __name__ == "__main__":
    main()
