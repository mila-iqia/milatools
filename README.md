
# milatools

The milatools package provides the `mila` command, which is meant to help with connecting to and interacting with the Mila cluster.

---

**Warning**

The `mila` command is meant to be used on your local machine. Trying to run it on the cluster will fail with an error

---


## Install

Requires Python >= 3.8

We recommend using [`uv`](https://docs.astral.sh/uv/) in order to use `milatools` in its own isolated environment instead of alterring your default Python environment or creating and activating a new one manually.

```bash
uv tool install milatools
```

_Alternatively_:

```bash
pip install milatools
```

Or, for bleeding edge version:

```bash
uv tool install git+https://github.com/mila-iqia/milatools.git
# OR
pip install git+https://github.com/mila-iqia/milatools.git
```

After installing `milatools`, start with `mila init`:

```bash
mila init
```


## Commands

### mila init

Set up your access to the mila cluster interactively. Have your username and password ready!

* Set up your SSH config for easy connection with `ssh mila`
* Set up your public key if you don't already have them
* Copy your public key over to the cluster for passwordless auth
* Set up a public key on the login node to enable ssh into compute nodes
* **new**: Add a special SSH config for direct connection to a **compute node** with `ssh mila-cpu`


### mila docs/intranet

* Use `mila docs <search terms>` to search the Mila technical documentation
* Use `mila intranet <search terms>` to search the Mila intranet

Both commands open a browser window. If no search terms are given you are taken to the home page.


### mila code

Connect a VSCode instance to a compute node. `mila code` first allocates a compute node using slurm (you can pass slurm options as well using `--alloc`), and then calls the `code` command with the appropriate options to start a remote coding session on the allocated node.

You can simply Ctrl+C the process to end the session.

```
usage: mila code [-h] [--cluster {mila,cedar,narval,beluga,graham}] [--alloc ...]
                 [--command VALUE] [--job VALUE] [--node VALUE] [--persist]
                 PATH

positional arguments:
  PATH                  Path to open on the remote machine

options:
  -h, --help            show this help message and exit
  --alloc ...           Extra options to pass to slurm
  --cluster {mila,cedar,narval,beluga,graham}
                        Which cluster to connect to.
  --command VALUE       Command to use to start vscode (defaults to "code" or the value
                        of $MILATOOLS_CODE_COMMAND)
  --job VALUE           Job ID to connect to
  --node VALUE          Node to connect to
  --persist             Whether the server should persist or not
```

For example:

```bash
mila code path/to/my/experiment
```

The `--alloc` option may be used to pass extra arguments to `salloc` when allocating a node (for example, `--alloc --gres=gpu:1` to allocate 1 GPU). `--alloc` should be at the end, because it will take all of the arguments that come after it.

If you already have an allocation on a compute node, you may use the `--node NODENAME` or `--job JOBID` options to connect to that node.

#### SLURM environment variables in VS Code terminals

VS Code connects to the compute node in its own SSH session, outside of the SLURM job, so its terminals would normally only have `SLURM_JOB_ID` set (injected by the cluster when the session is adopted into the job) and none of the other `SLURM_*` variables (`SLURM_NTASKS`, `SLURM_TMPDIR`, etc.).

To fix this, `mila code` saves the job's SLURM environment variables to a file (under `~/.cache/milatools/slurm_env/` on the cluster) when it creates the job, and installs a clearly-marked block in your `~/.bashrc` (and `~/.zshrc`, if you have one) on the cluster that restores them in VS Code terminals. The block is inert everywhere else: it only runs in sessions that have `SLURM_JOB_ID` but are missing the rest of the job's environment, and never overwrites variables in regular `salloc`/`srun` shells or job scripts.

Known limitations: `fish` shells don't get the variables (the block is only installed for bash/zsh), and they are only available in the integrated terminals, not in the VS Code server process itself. Jobs connected to with `--job`/`--node` that weren't created by `mila code` don't have a saved environment.


### mila serve

The purpose of `mila serve` is to make it easier to start notebooks, logging servers, etc. on the compute nodes and connect to them.

```
usage: mila serve [-h] {connect,kill,list,lab,notebook,tensorboard,mlflow,aim} ...

positional arguments:
  {connect,kill,list,lab,notebook,tensorboard,mlflow,aim}
    connect             Reconnect to a persistent server.
    kill                Kill a persistent server.
    list                List active servers.
    lab                 Start a Jupyterlab server.
    notebook            Start a Jupyter Notebook server.
    tensorboard         Start a Tensorboard server.
    mlflow              Start an MLFlow server.
    aim                 Start an AIM server.

optional arguments:
  -h, --help            show this help message and exit
```

For example, to start jupyterlab with one GPU, you may write:

```bash
mila serve lab --alloc --gres gpu:1
```

You can of course write any SLURM arguments after `--alloc`.

Ending the connection will end the server, but the `--persist` flag can be used to prevent that. In that case you would be able to write `mila serve connect jupyter-lab` in order to reconnect to your running instance. Use `mila serve list` and `mila serve kill` to view and manage any running instances.
