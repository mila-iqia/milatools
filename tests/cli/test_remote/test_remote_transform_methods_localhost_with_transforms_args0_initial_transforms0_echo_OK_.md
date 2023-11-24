After creating a Remote like so:

```python
remote = Remote('localhost', connection=Connection('localhost'), transforms=())
```

and then calling:

```python
transformed_remote = remote.with_transforms(
    lambda cmd: cmd.replace("OK", "NOT_OK"),
    lambda cmd: f"echo 'command before' && {cmd}",
)
result = transformed_remote.run('echo OK')
```

Printed the following on the terminal:

```console
(localhost) $ echo OK
command before
NOT_OK

```

The command that eventually would be run on the cluster is:

```bash
echo 'command before' && echo NOT_OK
```

and `result.stdout.strip()='command before\nNOT_OK'`.
