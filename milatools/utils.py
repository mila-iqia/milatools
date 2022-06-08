import re
import shlex
import subprocess
import tempfile
from pathlib import Path
from queue import Empty, Queue

import blessed
import questionary as qn
from fabric import Connection
from sshconf import read_ssh_config

here = Path(__file__).parent

style = qn.Style(
    [
        ("envname", "yellow bold"),
        ("envpath", "cyan"),
        ("prefix", "bold"),
        ("special", "orange bold"),
        ("cancel", "grey bold"),
    ]
)


def yn(prompt, default=True):
    return qn.confirm(prompt, default=default).unsafe_ask()


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
        fieldmap = {
            "cd": "cd",
            "put": "putfile",
            "get": "getfile",
        }
        for infield, outfield in fieldmap.items():
            setattr(self, outfield, getattr(self.conn, infield))

    def display(self, cmd):
        print(T.bold_cyan(f"({self.host}) $ ", cmd))

    def run(
        self, cmd, display=True, bash=False, precommand=None, profile=None, **kwargs
    ):
        if profile is not None:
            precommand = f"source {profile}"
        if display:
            self.display(cmd)
        if precommand:
            cmd = f"{precommand} && {cmd}"
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

    def puttext(self, text, dest):
        base = Path(dest).parent
        self.run(f"mkdir -p {base}", display=False, hide=True)
        with tempfile.NamedTemporaryFile("w") as f:
            f.write(text)
            f.flush()
            self.putfile(f.name, dest)

    def run_script(self, name, *args, **kwargs):
        base = ".milatools/scripts"
        dest = f"{base}/{name}"
        print(T.bold_cyan(f"({self.host}) WRITE ", dest))
        self.run(f"mkdir -p {base}", display=False, hide=True)
        self.putfile(here / name, dest)
        return self.run(shjoin([dest, *args]), **kwargs)

    def extract_script(self, name, *args, pattern, **kwargs):
        base = ".milatools/scripts"
        dest = f"{base}/{name}"
        print(T.bold_cyan(f"({self.host}) WRITE ", dest))
        self.run(f"mkdir -p {base}", display=False, hide=True)
        self.putfile(here / name, dest)
        return self.extract(shjoin([dest, *args]), pattern=pattern, **kwargs)


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
