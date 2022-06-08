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


class CommandNotFoundError(Exception):

    # Instructions to install certain commands if they are not found
    instructions = {
        "code": (
            "To fix this, try starting VSCode, then hit Cmd+Shift+P,"
            " type 'install code command' in the box, and hit Enter."
            " You might need to restart your shell."
        )
    }

    def __init__(self, command):
        super().__init__(command)

    def __str__(self):
        cmd = self.args[0]
        message = f"Command '{cmd}' does not exist locally."
        supp = self.instructions.get(cmd, None)
        if supp:
            message += f" {supp}"
        return message


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
        try:
            return subprocess.run(
                args,
                universal_newlines=True,
                **kwargs,
            )
        except FileNotFoundError as e:
            if e.filename == args[0]:
                raise CommandNotFoundError(e.filename)
            else:
                raise

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
    def __init__(self, hostname, connection=None, transforms=()):
        self.hostname = hostname
        if connection is None:
            connection = Connection(hostname)
        self.connection = connection
        self.transforms = transforms

    def with_transforms(self, *transforms):
        return Remote(
            hostname=self.hostname,
            connection=self.connection,
            transforms=(*self.transforms, *transforms),
        )

    def wrap(self, wrapper):
        return self.with_transforms(wrapper.format)

    def with_precommand(self, precommand):
        return self.wrap(f"{precommand} && {{}}")

    def with_profile(self, profile):
        return self.wrap(f"source {profile} && {{}}")

    def with_bash(self):
        return self.with_transforms(lambda cmd: shjoin(["bash", "-c", cmd]))

    def display(self, cmd):
        print(T.bold_cyan(f"({self.hostname}) $ ", cmd))

    def run(self, cmd, display=None, hide=False, **kwargs):
        if display is None:
            display = not hide
        if display:
            self.display(cmd)
        for transform in self.transforms:
            cmd = transform(cmd)
        return self.connection.run(cmd, hide=hide, **kwargs)

    def get_output(self, cmd, **kwargs):
        return self.run(cmd, **kwargs).stdout.strip()

    def get_lines(self, cmd, **kwargs):
        return self.get_output(cmd, **kwargs).split()

    def extract(self, cmd, patterns, wait=False, **kwargs):
        kwargs.setdefault("pty", True)
        qio = QueueIO()
        proc = self.run(cmd, asynchronous=True, out_stream=qio, **kwargs)
        results = {}
        try:
            for line in qio.readlines(lambda: proc.runner.process_is_finished):
                print(line, end="")
                for name, patt in list(patterns.items()):
                    m = re.search(patt, line)
                    if m:
                        results[name] = m.groups()[0]
                        patterns.pop(name)
                        if not patterns and not wait:
                            return proc.runner, results
        except KeyboardInterrupt:
            proc.runner.kill()
            raise
        proc.join()
        return proc.runner, results

    def get(self, src, dest):
        return self.connection.get(src, dest)

    def put(self, src, dest):
        return self.connection.put(src, dest)

    def puttext(self, text, dest):
        base = Path(dest).parent
        self.run(f"mkdir -p {base}", display=False, hide=True)
        with tempfile.NamedTemporaryFile("w") as f:
            f.write(text)
            f.flush()
            self.put(f.name, dest)

    def home(self):
        return self.get_output("echo $HOME", hide=True)

    def ensure_allocation(self):
        return self.hostname, None

    def run_script(self, name, *args, **kwargs):
        base = ".milatools/scripts"
        dest = f"{base}/{name}"
        print(T.bold_cyan(f"({self.host}) WRITE ", dest))
        self.run(f"mkdir -p {base}", hide=True)
        self.put(here / name, dest)
        return self.run(shjoin([dest, *args]), **kwargs)

    def extract_script(self, name, *args, pattern, **kwargs):
        base = ".milatools/scripts"
        dest = f"{base}/{name}"
        print(T.bold_cyan(f"({self.host}) WRITE ", dest))
        self.run(f"mkdir -p {base}", hide=True)
        self.put(here / name, dest)
        return self.extract(shjoin([dest, *args]), pattern=pattern, **kwargs)


class SlurmRemote(Remote):
    def __init__(self, connection, alloc, transforms=()):
        self.alloc = alloc
        super().__init__(
            hostname="->",
            connection=connection,
            transforms=[
                *transforms,
                lambda cmd: shjoin(["srun", *self.alloc, "bash", "-c", cmd]),
            ],
        )

    def with_transforms(self, *transforms):
        return SlurmRemote(
            connection=self.connection,
            alloc=self.alloc,
            transforms=[*self.transforms[:-1], *transforms],
        )

    def ensure_allocation(self):
        remote = Remote(hostname="->", connection=self.connection).with_bash()
        proc, results = remote.extract(
            shjoin(["salloc", *self.alloc]),
            patterns={"node_name": "salloc: Nodes ([^ ]+) are ready for job"},
        )
        return results["node_name"], proc


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
