Running the `mila init` command with this initial content:

```
# a comment

Host foo
  HostName foobar.com




# another comment after lots of empty lines.

```

and these user inputs: ('n', 'y', 'bob_drac\r', 'y')
leads the following ssh config file:

```
# a comment

Host foo
  HostName foobar.com




# another comment after lots of empty lines.

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
