import os
import shlex
import subprocess
import webbrowser

from coleo import Option, auto_cli, default, tooled

from .utils import Local, SSHConfig, SSHConnection, T, yn
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
        url = "https://docs.mila.quebec"
        print(f"Opening the docs: {url}")
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
                while not (username := input(T.bold("What is your username?\n> "))):
                    continue
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

        ssh = SSHConnection("mila")
        try:
            pubkeys = ssh.get("ls -t ~/.ssh/id*.pub").strip().split()
            print("# OK")
        except subprocess.CalledProcessError:
            print("# MISSING")
            if yn("You have no public keys on the login node. Generate them?"):
                # print("(Note: You can just press Enter 3x to accept the defaults)")
                # _, keyfile = ssh.extract("ssh-keygen", pattern="Your public key has been saved in ([^ ]+)", wait=True)
                private_file = "~/.ssh/id_rsa"
                ssh.get(f'ssh-keygen -q -t rsa -N "" -f {private_file}')
                pubkeys = [f"{private_file}.pub"]
            else:
                exit("Cannot proceed because there is no public key")

        common = ssh.get(
            "comm -12 <(sort ~/.ssh/authorized_keys) <(sort ~/.ssh/*.pub)", bash=True
        ).strip()
        if common:
            print("# OK")
        else:
            print("# MISSING")
            if yn(
                "To connect to a compute node from a login node you need one id_*.pub to be in authorized_keys. Do it?"
            ):
                pubkey = pubkeys[0]
                ssh.get(f"cat {pubkey} >> ~/.ssh/authorized_keys")

    def code():
        """Open a remote VSCode session on a compute node."""
        # Path to open on the remote machine
        # [positional]
        path: Option

        ssh = SSHConnection("mila")
        here = Local()

        proc, node_name = _find_allocation(ssh)

        if not path.startswith("/"):
            # Get $HOME because we have to give the full path to code
            home = ssh.get("echo $HOME").strip()
            print("#", home)
            path = os.path.join(home, path)

        here.run("code", "--remote", f"ssh-remote+{node_name}.server.mila.quebec", path)

        try:
            if proc is not None:
                proc.wait()
        except KeyboardInterrupt:
            print(f"Ended session on '{node_name}'")
            ssh.cleanup()
            exit()


@tooled
def _find_allocation(ssh):
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
        node_name = ssh.get(f"squeue --jobs {job} -ho %N").strip()
        print("#", node_name)

    else:
        node_name = None
        proc, node_name = ssh.extract(
            shlex.join(["salloc", *alloc]),
            pattern="salloc: Nodes ([^ ]+) are ready for job\n",
            bash=True,  # Some zsh or fish shells may be improperly configured for salloc
        )

    if node_name is None:
        exit("ERROR: Could not find the node name for the allocation")

    return proc, node_name
