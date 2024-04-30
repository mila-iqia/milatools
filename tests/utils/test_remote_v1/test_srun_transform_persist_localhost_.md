After creating a SlurmRemote like so:

```python
remote = SlurmRemote(
    Connection('localhost'),
    alloc=['--time=00:01:00'],
    transforms=(),
    persist=False,
)
```

Calling this:
```python
remote.srun_transform_persist('bob')
```

created a new sbatch script with this content (with some substitutions for regression tests):
```
#!/bin/bash
#SBATCH --output=$HOME/.milatools/batch/out-1234567890.txt
#SBATCH --ntasks=1

echo jobid = $SLURM_JOB_ID >> /dev/null

bob

```

and produced the following command as output (with the absolute path to the home directory replaced with '$HOME' for tests):

```bash
cd $SCRATCH && sbatch --time=00:01:00 $HOME/.milatools/batch/batch-1234567890.sh; touch $HOME/.milatools/batch/out-1234567890.txt; tail -n +1 -f $HOME/.milatools/batch/out-1234567890.txt
```
