from subprocess import CompletedProcess

cmdtest = """===============
Captured stdout
===============
{cout}
===============
Captured stderr
===============
{cerr}
=============
Result stdout
=============
{out}
=============
Result stderr
=============
{err}
"""


def output_tester(func, capsys, file_regression):
    out, err = None, None
    try:
        out, err = func()
        if isinstance(out, CompletedProcess):
            out, err = out.stdout, out.stderr
    finally:
        captured = capsys.readouterr()
        out = out if out else ""
        err = err if err else ""
        file_regression.check(
            cmdtest.format(cout=captured.out, cerr=captured.err, out=out, err=err)
        )
