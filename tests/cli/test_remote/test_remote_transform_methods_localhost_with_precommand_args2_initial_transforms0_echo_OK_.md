After creating a Remote like so:

```python
remote = Remote('localhost', connection=Connection('localhost'), transforms=())
```

and then calling:

```python
transformed_remote = remote.with_precommand("echo 'echo precommand'")
result = transformed_remote.run('echo OK')
```

Printed the following on the terminal:

```console
(localhost) $ echo OK
echo precommand
OK

```

The command that eventually would be run on the cluster is:

```bash
echo 'echo precommand' && echo OK
```

and `result.stdout.strip()='echo precommand\nOK'`.
