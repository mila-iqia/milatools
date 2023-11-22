After creating a Remote like so:

```python
remote = Remote('mila', connection=Connection('mila'), transforms=())
```

and then calling:

```python
transformed_remote = remote.wrap("echo 'echo wrap' && {}")
transformed_remote.run('echo OK')
```

Printed the following on the terminal:

```console
(mila) $ echo OK

```

The command that eventually would be run on the cluter is:

```bash
echo 'echo wrap' && echo OK
```
