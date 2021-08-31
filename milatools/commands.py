import os
import shlex
import subprocess
import sys

from coleo import Option, auto_cli

from .utils import SSHCon, SSHConfig, T, check_passwordless, yn


def command_init():

    #############################
    # Step 1: SSH Configuration #
    #############################

    print("Checking ssh config")

    c = SSHConfig()
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
        if yn("There is no '*.server.mila.quebec' entry in ~/.ssh/config. Create one?"):
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

    # Check that there is an id file

    sshdir = os.path.expanduser("~/.ssh")
    if not any(
        entry.startswith("id") and entry.endswith(".pub")
        for entry in os.listdir(sshdir)
    ):
        if yn("You have no public keys. Generate one?"):
            print(T.bold_green("(local) $ ssh-keygen"))
            subprocess.run(["ssh-keygen"])
        else:
            exit("No public keys.")

    # Check that it is possible to connect using the key

    if not check_passwordless("mila"):
        if yn(
            "Your public key does not appear be registered on the cluster. Register it?"
        ):
            print(T.bold_green("(local) $ ssh-copy-id mila"))
            subprocess.run(["ssh-copy-id", "mila"])
            if not check_passwordless("mila"):
                exit("ssh-copy-id appears to have failed")
        else:
            exit("No passwordless login.")

    #####################################
    # Step 3: Set up keys on login node #
    #####################################

    print("Checking connection to compute nodes")

    ssh = SSHCon("mila")
    try:
        pubkeys = ssh.get("ls -t ~/.ssh/id*.pub").strip().split()
        print("# OK")
    except subprocess.CalledProcessError:
        print("# MISSING")
        if yn("You have no public keys on the login node. Generate them?"):
            print("(Note: You can just press Enter 3x to accept the defaults)")
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


def command_code():
    # [positional]
    path: Option

    # [remainder]
    slurm_opts: Option

    ssh = SSHCon("mila")

    node_name = None
    proc, node_name = ssh.extract(
        shlex.join(["salloc", *slurm_opts]),
        pattern="salloc: Nodes ([^ ]+) are ready for job\n",
    )

    if node_name is None:
        exit("Could not find the node name for the allocation")

    if not path.startswith("/"):
        home = ssh.get("echo $HOME").strip()
        print("#", home)
        path = os.path.join(home, path)

    vscmd = ["code", "--remote", f"ssh-remote+{node_name}.server.mila.quebec", path]
    print(T.bold_green("(local) $ ", shlex.join(vscmd)))

    subprocess.run(vscmd)

    try:
        proc.wait()
    except KeyboardInterrupt:
        print(f"Ended session on '{node_name}'")
        ssh.cleanup()
        sys.exit()


def main():
    pfx = "command_"
    auto_cli(
        {
            k[len(pfx) :].replace("_", "-"): v
            for k, v in globals().items()
            if k.startswith(pfx)
        }
    )
