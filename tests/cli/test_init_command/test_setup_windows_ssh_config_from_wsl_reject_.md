When this SSH config is already present in the WSL environment with these initial contents:
```

Host mila
  HostName login.server.mila.quebec
  PreferredAuthentications publickey,keyboard-interactive
  Port 2222
  ServerAliveInterval 120
  ServerAliveCountMax 5
  User bob
  IdentityFile <WSL_HOME>/.ssh/id_rsa_mila

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
  IdentityFile <WSL_HOME>/.ssh/id_rsa_mila

Host *.server.mila.quebec !*login.server.mila.quebec
  ProxyJump mila
  User bob
  IdentityFile <WSL_HOME>/.ssh/id_rsa_mila

Host cn-????
  ProxyJump mila
  User bob
  IdentityFile <WSL_HOME>/.ssh/id_rsa_mila

Host narval rorqual fir nibi trillium trillium-gpu tamia killarney vulcan
  HostName %h.alliancecan.ca
  ControlMaster auto
  ControlPath <WSL_HOME>/.cache/ssh/%r@%h:%p
  ControlPersist yes
  User bob
  IdentityFile <WSL_HOME>/.ssh/id_rsa_drac

Host nc????? ng?????
  ProxyJump narval
  User bob
  IdentityFile <WSL_HOME>/.ssh/id_rsa_drac

Host rc????? rg????? rl?????
  ProxyJump rorqual
  User bob
  IdentityFile <WSL_HOME>/.ssh/id_rsa_drac

Host fc????? fb?????
  ProxyJump fir
  User bob
  IdentityFile <WSL_HOME>/.ssh/id_rsa_drac

Host c? c?? c??? g? g?? l? l?? m? m?? u?
  ProxyJump nibi
  User bob
  IdentityFile <WSL_HOME>/.ssh/id_rsa_drac

Host tg????? tc?????
  ProxyJump tamia
  User bob
  IdentityFile <WSL_HOME>/.ssh/id_rsa_drac

Host kn???
  ProxyJump killarney
  User bob
  IdentityFile <WSL_HOME>/.ssh/id_rsa_drac

Host rack??-??
  ProxyJump vulcan
  User bob
  IdentityFile <WSL_HOME>/.ssh/id_rsa_drac

Host !trillium tri????
  ProxyJump trillium
  User bob
  IdentityFile <WSL_HOME>/.ssh/id_rsa_drac

Host !trillium trig????
  ProxyJump trillium-gpu
  User bob
  IdentityFile <WSL_HOME>/.ssh/id_rsa_drac
```


and this user input: n
leads the following ssh config file on the Windows side:

```

```