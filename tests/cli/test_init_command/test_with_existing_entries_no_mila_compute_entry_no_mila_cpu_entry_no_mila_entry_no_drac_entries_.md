Running the `mila init` command with no initial ssh config file

and these user inputs: ['bob\r', 'y', 'bob\r', 'y']
leads to the following ssh config file:

```

Host mila
  HostName login.server.mila.quebec
  PreferredAuthentications publickey,keyboard-interactive
  Port 2222
  ServerAliveInterval 120
  ServerAliveCountMax 5
  ControlMaster auto
  ControlPath ~/.cache/ssh/%r@%h:%p
  ControlPersist 600
  User bob

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
  User bob

Host *.server.mila.quebec !*login.server.mila.quebec
  HostName %h
  ProxyJump mila
  ControlMaster auto
  ControlPath ~/.cache/ssh/%r@%h:%p
  ControlPersist 600
  User bob

Host beluga cedar graham narval niagara
  HostName %h.computecanada.ca
  ControlMaster auto
  ControlPath ~/.cache/ssh/%r@%h:%p
  ControlPersist 600
  User bob

Host !beluga  bc????? bg????? bl?????
  ProxyJump beluga
  ControlMaster auto
  ControlPath ~/.cache/ssh/%r@%h:%p
  ControlPersist 600
  User bob

Host !cedar   cdr? cdr?? cdr??? cdr????
  ProxyJump cedar
  ControlMaster auto
  ControlPath ~/.cache/ssh/%r@%h:%p
  ControlPersist 600
  User bob

Host !graham  gra??? gra????
  ProxyJump graham
  ControlMaster auto
  ControlPath ~/.cache/ssh/%r@%h:%p
  ControlPersist 600
  User bob

Host !narval  nc????? ng?????
  ProxyJump narval
  ControlMaster auto
  ControlPath ~/.cache/ssh/%r@%h:%p
  ControlPersist 600
  User bob

Host !niagara nia????
  ProxyJump niagara
  ControlMaster auto
  ControlPath ~/.cache/ssh/%r@%h:%p
  ControlPersist 600
  User bob
```