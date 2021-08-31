import os
import re
import shlex
import subprocess

import blessed
from sshconf import read_ssh_config

sockdir = os.path.expanduser("~/.ssh/sockets")


T = blessed.Terminal()


class SSHCon:
    def __init__(self, host):
        os.makedirs(sockdir, exist_ok=True)
        self.host = host
        self.sock = os.path.join(sockdir, f"milatools.{host}")
        sshcmd = ["ssh", host, "-fNMS", self.sock]
        print(T.bold_green(f"(local) ", shlex.join(sshcmd)))
        self.master = subprocess.Popen(
            sshcmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
        )

    def cmd(self, *args):
        return ["ssh", self.host, "-S", self.sock, *args]

    def display(self, args):
        print(T.bold_cyan(f"({self.host}) ", *args))

    def get(self, *args, bash=False):
        self.display(args)
        if bash:
            args = [shlex.join(["bash", "-c", *args])]
            cmd = self.cmd(*args)
        else:
            cmd = self.cmd(*args)
        return subprocess.check_output(
            cmd,
            universal_newlines=True,
        )

    def popen(self, *args):
        self.display(args)
        cmd = self.cmd(*args)
        return subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
        )

    def extract(self, *args, pattern, wait=False):
        proc = self.popen(*args)
        result = None
        while line := proc.stdout.readline():
            print("#", line.rstrip())
            if m := re.match(pattern, line):
                result = m.groups()[0]
                if not wait:
                    return proc, result
        proc.wait()
        return None, result

    def wait(self):
        self.master.wait()

    def cleanup(self):
        pass


def check_passwordless(host):
    print(T.bold_green(f"(local) $ ssh -oBatchMode=yes {host} 'echo OK'"))
    results = subprocess.run(
        ["ssh", "-oBatchMode=yes", host, "echo OK"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    if results.returncode != 0:
        if "Permission denied" in results.stderr:
            return False
        else:
            print(results.stdout)
            print(results.stderr)
            exit("Failed to connect to mila, could not understand error")
    else:
        print("# OK")
        return True


def yn(question, default="y"):
    options = "[y/n]".replace(default, default.upper())
    answer = input(T.bold(f"{question} {options} ")).strip()
    return (answer or default) in "yY"


class SSHConfig:
    def __init__(self):
        self.cfg = read_ssh_config(os.path.expanduser("~/.ssh/config"))
        self.add = self.cfg.add
        self.save = self.cfg.save
        self.host = self.cfg.host
        self.hosts = self.cfg.hosts

    def hoststring(self, host):
        lines = []
        for filename, cfg in self.cfg.configs_:
            lines += [line.line for line in cfg.lines_ if line.host == host]
        return "\n".join(lines)

    def confirm(self, host):
        print(T.bold("The following code will be appended to your ~/.ssh/config:\n"))
        print(self.hoststring(host))
        return yn("\nIs this OK?")
