After creating a Remote like so:

```python
remote = Remote('localhost', connection=Connection('localhost'), transforms=())
```

and then calling:

```python
transformed_remote = remote.with_profile('.bashrc')
transformed_remote.run('echo OK')
```

Printed the following on the terminal:

```console
(localhost) $ echo OK
OK

```

The command that eventually would be run on the cluster is:

```bash
source .bashrc && echo OK
```
