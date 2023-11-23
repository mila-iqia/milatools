After creating a Remote like so:

```python
remote = Remote('localhost', connection=Connection('localhost'), transforms=())
```

and then calling:

```python
transformed_remote = remote.wrap("echo 'echo wrap' && {}")
transformed_remote.run('echo OK')
```

Printed the following on the terminal:

```console
(localhost) $ echo OK
echo wrap
OK

```

The command that eventually would be run on the cluster is:

```bash
echo 'echo wrap' && echo OK
```
