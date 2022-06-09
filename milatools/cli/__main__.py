import os
import random
import subprocess
import time
import webbrowser
from pathlib import Path
from urllib.parse import urlencode

import questionary as qn
from coleo import Option, auto_cli, default, tooled

from ..version import version as mversion
from .profile import ensure_program, setup_profile
from .utils import Local, Remote, SlurmRemote, SSHConfig, T, yn


def main():
    """Entry point for milatools."""
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

        if "*.server.mila.quebec" not in c.hosts():
            if yn(
                "There is no '*.server.mila.quebec' entry in ~/.ssh/config. Create one?"
            ):
                username = c.host("mila")["user"]
                c.add(
                    "*.server.mila.quebec",
                    HostName="%h",
                    User=username,
                    ProxyJump="mila",
                )
                if not c.confirm("*.server.mila.quebec"):
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
            pubkeys = remote.get("ls -t ~/.ssh/id*.pub").split()
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

        common = remote.get(
            "comm -12 <(sort ~/.ssh/authorized_keys) <(sort ~/.ssh/*.pub)", bash=True
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
            path = os.path.join(home, path)

        time.sleep(1)
        try:
            here.run(
                "code",
                "-nw",
                "--remote",
                f"ssh-remote+{node_name}.server.mila.quebec",
                path,
            )
        except KeyboardInterrupt:
            pass
        if proc is not None:
            proc.kill()
        print(f"Ended session on '{node_name}'")

    class serve:
        def jupyter():
            """Start a Jupyter Notebook server."""

            # Path to open on the remote machine
            # [positional: ?]
            path: Option = default(None)

            remote = Remote("mila")

            path = path or "~"
            pathdir = path
            if path.endswith(".ipynb"):
                pathdir = str(Path(path).parent)

            prof = setup_profile(remote, pathdir)
            premote = remote.with_profile(prof)

            ensure_program(
                remote=premote,
                program="jupyter",
                installers={
                    "conda": "conda install -y jupyter",
                    "pip": "pip install jupyter",
                },
            )

            remote.run("mkdir -p ~/.milatools/sockets", hide=True)

            cnode = _find_allocation(remote)
            proc, results = cnode.with_profile(prof).extract(
                f"echo '####' $(hostname) && jupyter notebook --sock ~/.milatools/sockets/$(hostname).sock {pathdir}",
                patterns={
                    "node_name": "#### ([A-Za-z0-9_-]+)",
                    "url": "Notebook is listening on (.*)",
                    "token": "token=([a-f0-9]+)",
                },
            )
            node_name = results["node_name"]

            local_proc = _forward(
                local=Local(),
                node=f"{node_name}.server.mila.quebec",
                to_forward=f"{remote.home()}/.milatools/sockets/{node_name}.sock",
                options={"token": results["token"]},
            )

            try:
                local_proc.wait()
            except KeyboardInterrupt:
                exit("Terminated by user.")
            finally:
                local_proc.kill()
                proc.kill()

        def tensorboard():
            """Start a Tensorboard server."""

            # Path to the experiment logs
            # [positional: ?]
            path: Option = default(None)

            remote = Remote("mila")

            pathdir = path or "~"

            prof = setup_profile(remote, pathdir)
            premote = remote.with_profile(prof)

            ensure_program(
                remote=premote,
                program="tensorboard",
                installers={
                    "conda": "conda install -y tensorboard",
                    "pip": "pip install tensorboard",
                },
            )

            cnode = _find_allocation(remote)
            proc, results = cnode.with_profile(prof).extract(
                f"echo '####' $(hostname) && tensorboard --logdir {pathdir} --port 0",
                patterns={
                    "node_name": "#### ([A-Za-z0-9_-]+)",
                    "port": "TensorBoard [^ ]+ at http://localhost:([0-9]+)/",
                },
            )
            node_name = results["node_name"]

            local_proc = _forward(
                local=Local(),
                node=f"{node_name}.server.mila.quebec",
                to_forward=int(results["port"]),
            )

            try:
                local_proc.wait()
            except KeyboardInterrupt:
                exit("Terminated by user.")
            finally:
                local_proc.kill()
                proc.kill()

        def mlflow():
            """Start an MLFlow server."""

            # Path to the experiment logs
            # [positional: ?]
            path: Option = default(None)

            remote = Remote("mila")

            pathdir = path or "~"

            prof = setup_profile(remote, pathdir)
            premote = remote.with_profile(prof)

            ensure_program(
                remote=premote,
                program="mlflow",
                installers={
                    "conda": "conda install -y mlflow",
                    "pip": "pip install mlflow",
                },
            )

            cnode = _find_allocation(remote)
            proc, results = cnode.with_profile(prof).extract(
                f"echo '####' $(hostname) && mlflow ui --backend-store-uri {pathdir} --port 0",
                patterns={
                    "node_name": "#### ([A-Za-z0-9_-]+)",
                    "port": "Listening at: http://127.0.0.1:([0-9]+)",
                },
            )
            node_name = results["node_name"]

            local_proc = _forward(
                local=Local(),
                node=f"{node_name}.server.mila.quebec",
                to_forward=int(results["port"]),
            )

            try:
                local_proc.wait()
            except KeyboardInterrupt:
                exit("Terminated by user.")
            finally:
                local_proc.kill()
                proc.kill()

        def aim():
            """Start an AIM server."""

            # Path to the experiment logs
            # [positional: ?]
            path: Option = default(None)

            # Remote port to use
            remote_port: Option = default(None)

            if remote_port is None:
                remote_port = random.randint(10000, 60000)

            remote = Remote("mila")

            pathdir = path or "~"

            prof = setup_profile(remote, pathdir)
            premote = remote.with_profile(prof)

            ensure_program(
                remote=premote,
                program="aim",
                installers={
                    "conda": "conda install -y aim",
                    "pip": "pip install aim",
                },
            )

            cnode = _find_allocation(remote)
            proc, results = cnode.with_profile(prof).extract(
                f"echo '####' $(hostname) && aim up --repo {pathdir} --port {remote_port}",
                patterns={
                    "node_name": "#### ([A-Za-z0-9_-]+)",
                    "port": "Open http://127.0.0.1:([0-9]+)",
                },
            )
            node_name = results["node_name"]

            local_proc = _forward(
                local=Local(),
                node=f"{node_name}.server.mila.quebec",
                to_forward=int(results["port"]),
            )

            try:
                local_proc.wait()
            except KeyboardInterrupt:
                exit("Terminated by user.")
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
def _forward(local, node, to_forward, page=None, options={}):

    # Port to open on the local machine
    port: Option = default(10101)

    if isinstance(to_forward, int):
        to_forward = f"localhost:{to_forward}"

    proc = local.popen(
        "ssh",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "StrictHostKeyChecking=no",
        "-nNL",
        f"localhost:{port}:{to_forward}",
        node,
    )

    time.sleep(2)

    url = f"http://localhost:{port}"
    if page is not None:
        if not page.startswith("/"):
            page = f"/{page}"
        url += page
    if options:
        url += f"?{urlencode(options)}"

    webbrowser.open(url)
    return proc


if __name__ == "__main__":
    main()
