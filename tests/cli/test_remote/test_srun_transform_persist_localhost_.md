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

created the following files:
- ~/.milatools/batch/batch-1234567890.sh:



```

#!/bin/bash
#SBATCH --output=.milatools/batch/out-1234567890.txt
#SBATCH --ntasks=1

echo jobid = $SLURM_JOB_ID >> /dev/null

bob


```

and produced the following command as output:

```bash
cd ~/scratch && sbatch --time=00:01:00 '~/.milatools/batch/batch-1234567890.sh'; touch .milatools/batch/out-1234567890.txt; tail -n +1 -f .milatools/batch/out-1234567890.txt
```
