Running the `mila init` command with this initial content:

```
# a comment

Host foo
  HostName foobar.com




# another comment after lots of empty lines.

```

and these user inputs: ('bob\r', 'n')
leads the following ssh config file:

```
# a comment

Host foo
  HostName foobar.com




# another comment after lots of empty lines.

```
