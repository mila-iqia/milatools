Running the `mila init` command with this initial content:

```
# A comment in the file.

```

and these user inputs: ('y', 'bob_mila\r', 'n', 'y')
leads the following ssh config file:

```
# A comment in the file.

Host mila
  HostName login.server.mila.quebec
  PreferredAuthentications publickey,keyboard-interactive
  Port 2222
  ServerAliveInterval 120
  ServerAliveCountMax 5
  ControlMaster auto
  ControlPath ~/.cache/ssh/%r@%h:%p
  ControlPersist yes
  User bob_mila

Host mila-cpu
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
  User bob_mila

Host *.server.mila.quebec !*login.server.mila.quebec
  HostName %h
  ProxyJump mila
  StrictHostKeyChecking no
  ControlMaster auto
  ControlPath ~/.cache/ssh/%r@%h:%p
  ControlPersist yes
  User bob_mila
```
