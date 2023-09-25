Running the `mila init` command with this initial content:

```
Host mila
  HostName login.server.mila.quebec
  User bob

Host mila-cpu
  HostName login.server.mila.quebec

```

and these user inputs: ['y']
leads to the following ssh config file:

```
Host mila
  HostName login.server.mila.quebec
  User bob
  PreferredAuthentications publickey,keyboard-interactive
  Port 2222
  ServerAliveInterval 120
  ServerAliveCountMax 5
  ControlMaster auto
  ControlPath ~/.cache/ssh/%r@%h:%p
  ControlPersist 600

Host mila-cpu
  HostName login.server.mila.quebec
  User bob
  Port 2222
  ForwardAgent yes
  StrictHostKeyChecking no
  LogLevel ERROR
  UserKnownHostsFile /dev/null
  RequestTTY force
  ConnectTimeout 600
  ServerAliveInterval 120
  ProxyCommand ssh mila "/cvmfs/config.mila.quebec/scripts/milatools/slurm-proxy.sh mila-cpu --mem=8G"
  RemoteCommand /cvmfs/config.mila.quebec/scripts/milatools/entrypoint.sh mila-cpu

Host *.server.mila.quebec !*login.server.mila.quebec
  HostName %h
  User bob
  ProxyJump mila
  ControlMaster auto
  ControlPath ~/.cache/ssh/%r@%h:%p
  ControlPersist 600

```