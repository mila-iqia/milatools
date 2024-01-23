Running the `mila init` command with this initial content:

```

# Compute Canada
Host beluga cedar graham narval niagara
  Hostname %h.alliancecan.ca
  User bob
Host mist
  Hostname mist.scinet.utoronto.ca
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

and these user inputs: ['bob\r', 'y']
leads to the following ssh config file:

```

# Compute Canada
Host beluga cedar graham narval niagara
  Hostname %h.alliancecan.ca
  User bob
  ControlMaster auto
  ControlPath ~/.cache/ssh/%r@%h:%p
  ControlPersist 600
Host mist
  Hostname mist.scinet.utoronto.ca
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
```