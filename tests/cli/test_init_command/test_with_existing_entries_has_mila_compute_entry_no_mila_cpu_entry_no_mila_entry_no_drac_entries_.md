Running the `mila init` command with this initial content:

```
Host *.server.mila.quebec !*login.server.mila.quebec
  HostName foooobar.com

```

and these user inputs: ['bob\r', 'y', 'bob\r', 'y']
leads to the following ssh config file:

```
Host *.server.mila.quebec !*login.server.mila.quebec
  HostName %h
  User bob
  ProxyJump mila
  ForwardAgent yes
  ForwardX11 yes
  ControlMaster auto
  ControlPath ~/.cache/ssh/%r@%h:%p
  ControlPersist 600

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
```