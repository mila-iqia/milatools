Running the `mila init` command with this initial content:

```
# a comment
Host foo
  HostName foobar.com

# another comment

```

and these user inputs: ('n', 'n', 'n')
leads the following ssh config file:

```
# a comment
Host foo
  HostName foobar.com

# another comment

```
