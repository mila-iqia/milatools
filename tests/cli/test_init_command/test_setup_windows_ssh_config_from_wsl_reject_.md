When this SSH config is already present in the WSL environment with these initial contents:
```

Host beluga cedar graham narval niagara
  HostName %h.computecanada.ca
  User bob

Host mist
  HostName mist.scinet.utoronto.ca
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
  User bob
  PreferredAuthentications publickey,keyboard-interactive
  Port 2222
  ServerAliveInterval 120
  ServerAliveCountMax 5
  ControlMaster auto
  ControlPath ~/.cache/ssh/%r@%h:%p
  ControlPersist 600

Host mila-cpu
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
  ForwardAgent yes
  ForwardX11 yes
  ControlMaster auto
  ControlPath ~/.cache/ssh/%r@%h:%p
  ControlPersist 600
```


and these user inputs: ('y', 'n')
leads the following ssh config file on the Windows side:

```

```