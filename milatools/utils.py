import os
import re
import shlex
import subprocess

import blessed
from sshconf import read_ssh_config

sockdir = os.path.expanduser("~/.ssh/sockets")


T = blessed.Terminal()


class Local:
    def display(self, args):
        print(T.bold_green(f"(local) $ ", shlex.join(args)))

    def get(self, *args, **kwargs):
        self.display(args)
        return subprocess.check_output(
            args,
            universal_newlines=True,
            **kwargs,
        )

    def run(self, *args, **kwargs):
        self.display(args)
        return subprocess.run(
            args,
            universal_newlines=True,
            **kwargs,
        )

    def popen(self, *args, **kwargs):
        self.display(args)
        return subprocess.Popen(
            args,
            universal_newlines=True,
            **kwargs,
        )

    def check_passwordless(self, host):
        results = self.run(
            "ssh",
            "-oPreferredAuthentications=publickey",
            host,
            "echo OK",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
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


class SSHConnection:
    def __init__(self, host):
        self.here = Local()
        os.makedirs(sockdir, mode=0o700, exist_ok=True)
        self.host = host
        self.sock = os.path.join(sockdir, f"milatools.{host}")
        self.master = self.here.popen(
            "ssh",
            host,
            "-fNMS",
            self.sock,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

    def cmd(self, *args, bash=False):
        if bash:
            args = [shlex.join(["bash", "-c", *args])]
        return ["ssh", self.host, "-S", self.sock, *args]

    def display(self, args):
        print(T.bold_cyan(f"({self.host}) $ ", *args))

    def get(self, *args, bash=False):
        self.display(args)
        cmd = self.cmd(*args, bash=bash)
        return subprocess.check_output(
            cmd,
            universal_newlines=True,
        )

    def popen(self, *args, bash=False):
        self.display(args)
        cmd = self.cmd(*args, bash=bash)
        return subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
        )

    def extract(self, *args, pattern, wait=False, bash=False):
        proc = self.popen(*args, bash=bash)
        result = None
        try:
            while line := proc.stdout.readline():
                print("#", line.rstrip())
                if m := re.match(pattern, line):
                    result = m.groups()[0]
                    if not wait:
                        return proc, result
        except KeyboardInterrupt:
            proc.terminate()
            exit("Canceled")
        proc.wait()
        return None, result

    def wait(self):
        self.master.wait()

    def cleanup(self):
        pass


def yn(question, default="y"):
    """Ask a yes/no question."""
    options = "[y/n]".replace(default, default.upper())
    answer = input(T.bold(f"{question} {options} ")).strip()
    return (answer or default) in "yY"


class SSHConfig:
    """Wrapper around sshconf with some extra niceties."""

    def __init__(self, path):
        self.cfg = read_ssh_config(path)
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
