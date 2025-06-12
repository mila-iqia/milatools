Running the `mila init` command with this initial content:

```
# a comment
Host foo
    HostName foobar.com

```

and these user inputs: ('y', 'bob_mila\r', 'y', 'bob_drac\r', 'n')
leads the following ssh config file:

```
# a comment
Host foo
    HostName foobar.com

```
