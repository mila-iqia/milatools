import subprocess

from .utils import CommandNotFoundError, T, shjoin


class Local:
    def display(self, args):
        print(T.bold_green("(local) $ ", shjoin(args)))

    def silent_get(self, *args, **kwargs):
        return subprocess.check_output(
            args,
            universal_newlines=True,
            **kwargs,
        )

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
                exit(f"Failed to connect to {host}, could not understand error")
        else:
            print("# OK")
            return True
