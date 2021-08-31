
# milatools

The milatools package provides the `mila` command, which is meant to help with connecting to and interacting with the Mila cluster.


## Install

Requires Python >= 3.8

```bash
pip install git+git://github.com/mila-iqia/milatools.git
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


### mila code

Connect a VSCode instance to a compute node. `mila code` first allocates a compute node using slurm (you can pass slurm options as well), and then calls the `code` command with the appropriate options to start a remote coding session on the allocated node.

You can simply Ctrl+C the process to end the session.

```
usage: mila code [-h] PATH ...

positional arguments:
  PATH
  SLURM_OPTS

optional arguments:
  -h, --help  show this help message and exit
```

For example:

```bash
mila code path/to/my/experiment
```
