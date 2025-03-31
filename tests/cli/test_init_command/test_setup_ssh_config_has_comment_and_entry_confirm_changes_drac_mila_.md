Running the `mila init` command with this initial content:

```
# a comment
Host foo
  HostName foobar.com

# another comment

```

and these user inputs: ('y', 'bob_mila\r', 'y', 'bob_drac\r', 'y')
leads the following ssh config file:

```
# a comment
Host foo
  HostName foobar.com

# another comment

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

Host beluga cedar graham narval niagara
  HostName %h.alliancecan.ca
  ControlMaster auto
  ControlPath ~/.cache/ssh/%r@%h:%p
  ControlPersist yes
  User bob_drac

Host !beluga  bc????? bg????? bl?????
  ProxyJump beluga
  User bob_drac

Host !cedar   cdr? cdr?? cdr??? cdr????
  ProxyJump cedar
  User bob_drac

Host !graham  gra??? gra????
  ProxyJump graham
  User bob_drac

Host !narval  nc????? ng?????
  ProxyJump narval
  User bob_drac

Host !niagara nia????
  ProxyJump niagara
  User bob_drac
```
