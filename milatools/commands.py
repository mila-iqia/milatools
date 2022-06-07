import os
import subprocess
import time
import webbrowser
from pathlib import Path

import questionary as qn
from coleo import Option, auto_cli, default, tooled

from .profile import setup_profile
from .utils import Local, Remote, SSHConfig, T, shjoin
from .version import version as mversion


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
            if qn.confirm("There is no ~/.ssh/config file. Create one?").ask():
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
            if qn.confirm(
                "There is no 'mila' entry in ~/.ssh/config. Create one?"
            ).ask():
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
            if qn.confirm(
                "There is no '*.server.mila.quebec' entry in ~/.ssh/config. Create one?"
            ).ask():
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
            if qn.confirm("You have no public keys. Generate one?").ask():
                here.run("ssh-keygen")
            else:
                exit("No public keys.")

        # Check that it is possible to connect using the key

        if not here.check_passwordless("mila"):
            if qn.confirm(
                "Your public key does not appear be registered on the cluster. Register it?"
            ).ask():
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
            if qn.confirm(
                "You have no public keys on the login node. Generate them?"
            ).ask():
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
            if qn.confirm(
                "To connect to a compute node from a login node you need one id_*.pub to be in authorized_keys. Do it?"
            ).ask():
                pubkey = pubkeys[0]
                remote.run(f"cat {pubkey} >> ~/.ssh/authorized_keys")

    def code():
        """Open a remote VSCode session on a compute node."""
        # Path to open on the remote machine
        # [positional]
        path: Option

        remote = Remote("mila")
        here = Local()

        proc, node_name = _find_allocation(remote)

        if not path.startswith("/"):
            # Get $HOME because we have to give the full path to code
            home = remote.get("echo $HOME")
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
        proc.kill()
        print(f"Ended session on '{node_name}'")

    def jupyter():
        """Start a Jupyter server."""

        # Path to open on the remote machine
        # [positional]
        path: Option

        remote = Remote("mila")

        home = remote.get("echo $HOME")
        if not path.startswith("/"):
            path = os.path.join(home, path)

        prof = setup_profile(remote, path)

        progs = [
            Path(p).name
            for p in remote.get(
                "which jupyter pip conda",
                hide=True,
                display=False,
                profile=prof,
                warn=True,
            ).split()
        ]

        if "jupyter" not in progs:
            installers = {
                "conda": "conda install -y jupyter",
                "pip": "pip install jupyter",
            }
            choices = [
                *[cmd for prog, cmd in installers.items() if prog in progs],
                qn.Choice(title="I will install it myself.", value="<MYSELF>"),
            ]
            install = qn.select(
                "Jupyter is not installed in that environment. Do you want to install it?",
                choices=choices,
            ).ask()
            if install == "<MYSELF>":
                return
            else:
                remote.run(f"srun {install}", profile=prof)

        proc, node_name = _find_allocation(remote)

        time.sleep(1)
        node_full = f"{node_name}.server.mila.quebec"
        cnode = Remote(node_full)

        cnode.run("mkdir -p ~/.milatools/sockets")
        proc1, _ = cnode.extract(
            f"jupyter notebook --sock ~/.milatools/sockets/$(hostname).sock {path}",
            pattern="Notebook is listening on (.*)",
            profile=prof,
        )

        here = Local()
        local_proc = here.popen(
            "ssh",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "StrictHostKeyChecking=no",
            "-nNCL",
            f"localhost:8888:{home}/.milatools/sockets/{node_name}.sock",
            node_full,
        )

        time.sleep(2)
        webbrowser.open("http://localhost:8888")
        local_proc.wait()


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
        proc = None
        node_name = node

    elif job is not None:
        proc = None
        node_name = remote.get(f"squeue --jobs {job} -ho %N")

    else:
        node_name = None
        proc, node_name = remote.extract(
            shjoin(["salloc", *alloc]),
            pattern="salloc: Nodes ([^ ]+) are ready for job",
            bash=True,  # Some zsh or fish shells may be improperly configured for salloc
        )

    if node_name is None:
        exit("ERROR: Could not find the node name for the allocation")

    return proc, node_name
