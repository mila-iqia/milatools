Running the `mila init` command with this initial content:

```
Host mila-cpu
  HostName login.server.mila.quebec

```

and these user inputs: ['y', 'bob\r', 'y', 'bob\r', 'y']
leads to the following ssh config file:

```
Host mila-cpu
  HostName login.server.mila.quebec
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
  User bob

Host mila
  HostName login.server.mila.quebec
  PreferredAuthentications publickey,keyboard-interactive
  Port 2222
  ServerAliveInterval 120
  ServerAliveCountMax 5
  ControlMaster auto
  ControlPath ~/.cache/ssh/%r@%h:%p
  ControlPersist yes
  User bob

Host *.server.mila.quebec !*login.server.mila.quebec
  HostName %h
  ProxyJump mila
  ControlMaster auto
  ControlPath ~/.cache/ssh/%r@%h:%p
  ControlPersist yes
  User bob

Host beluga cedar graham narval niagara
  HostName %h.alliancecan.ca
  ControlMaster auto
  ControlPath ~/.cache/ssh/%r@%h:%p
  ControlPersist yes
  User bob

Host !beluga  bc????? bg????? bl?????
  ProxyJump beluga
  User bob

Host !cedar   cdr? cdr?? cdr??? cdr????
  ProxyJump cedar
  User bob

Host !graham  gra??? gra????
  ProxyJump graham
  User bob

Host !narval  nc????? ng?????
  ProxyJump narval
  User bob

Host !niagara nia????
  ProxyJump niagara
  User bob
```