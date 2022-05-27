import os
import re
import shlex
import subprocess
from queue import Empty, Queue

import blessed
from fabric import Connection
from sshconf import read_ssh_config

sockdir = os.path.expanduser("~/.ssh/sockets")


T = blessed.Terminal()


# This is the implementation of shlex.join in Python >= 3.8
def shjoin(split_command):
    """Return a shell-escaped string from *split_command*."""
    return " ".join(shlex.quote(arg) for arg in split_command)


class Local:
    def display(self, args):
        print(T.bold_green(f"(local) $ ", shjoin(args)))

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


class QueueIO:
    def __init__(self):
        self.q = Queue()

    def write(self, s):
        self.q.put(s)

    def flush(self):
        pass

    def readlines(self, stop):
        current = ""
        while True:
            try:
                current += self.q.get(timeout=0.05)
                if "\n" in current:
                    *lines, current = current.split("\n")
                for line in lines:
                    yield f"{line}\n"
            except Empty:
                if stop():
                    if current:
                        yield current
                    return


class Remote:
    def __init__(self, host):
        self.host = host
        self.conn = Connection(host)

    def display(self, cmd):
        print(T.bold_cyan(f"({self.host}) $ ", cmd))

    def run(self, cmd, display=True, bash=False, **kwargs):
        if display:
            self.display(cmd)
        if bash:
            cmd = shjoin(["bash", "-c", cmd])
        return self.conn.run(cmd, **kwargs)

    def get(self, cmd, display=True, bash=False, **kwargs):
        return self.run(cmd, display=display, bash=bash, **kwargs).stdout.strip()

    def extract(self, cmd, pattern, wait=False, bash=False, **kwargs):
        kwargs.setdefault("pty", True)
        qio = QueueIO()
        proc = self.run(cmd, bash=bash, asynchronous=True, out_stream=qio, **kwargs)
        result = None
        try:
            for line in qio.readlines(lambda: proc.runner.process_is_finished):
                print(line, end="")
                m = re.search(pattern, line)
                if m:
                    result = m.groups()[0]
                    if not wait:
                        return proc.runner, result
        except KeyboardInterrupt:
            proc.runner.kill()
            raise
        proc.join()
        return proc.runner, result


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
            args = [shjoin(["bash", "-c", *args])]
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
            while True:
                line = proc.stdout.readline()
                if not line:
                    break
                print("#", line.rstrip())
                m = re.match(pattern, line)
                if m:
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
