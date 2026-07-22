#!/bin/sh
# Managed by milatools (`mila code`) -- do not edit.
# Dumps the SLURM allocation-level environment of the current job to a
# per-job file, so that SSH sessions adopted into the job by pam_slurm_adopt
# (e.g. VS Code Remote-SSH terminals) can restore it. See the milatools block
# in ~/.bashrc.
#
# Step/task-specific variables are excluded: they describe the job step
# running this script, not the allocation, and sourcing them in a terminal
# would break `srun` commands run from it (e.g. SLURM_NTASKS=1 instead of
# the job's real task count).
# SLURM_EXPORT_ENV, SLURM_CLUSTERS, SLURM_HINT, SLURM_DEBUG_FLAGS,
# SLURM_EXIT_ERROR and SLURM_EXIT_IMMEDIATE are excluded because sbatch's
# and/or salloc's own "Input Environment Variables" docs list them as
# affecting those commands' behavior directly (aliases for --export,
# --clusters, --hint, debug/exit-code tuning), not job state, so leaking
# them into a terminal would silently change the behavior of sbatch/salloc/
# srun run from it. SLURM_CONF is cluster configuration, not job state
# (and must never be unset by the sbatch wrapper in the rc block).
#
# The sed pipeline turns `VAR=va'lue` into `export VAR='va'\''lue'`: values
# are single-quoted, with embedded single quotes escaped. (Limitation:
# values containing newlines would break this line-based processing; no
# SLURM variable contains newlines in practice.)
set -u
[ -n "${SLURM_JOB_ID:-}" ] || exit 0
dir="$HOME/.cache/milatools/slurm_env"
umask 077
mkdir -p "$dir" || exit 0
file="$dir/$SLURM_JOB_ID.env"
tmp="$file.tmp.$$"
printenv \
  | grep '^SLURM_' \
  | grep -Ev '^SLURM_(STEP|PROCID=|LOCALID=|NODEID=|GTIDS=|TASK_PID=|LAUNCH_NODE_IPADDR=|SRUN_COMM_|CPU_BIND|CPU_FREQ|DISTRIBUTION=|PTY_|TOPOLOGY_|UMASK=|EXPORT_ENV=|CONF=|CLUSTERS=|HINT=|DEBUG_FLAGS=|EXIT_ERROR=|EXIT_IMMEDIATE=)' \
  | sed -e "s/'/'\\\\''/g" -e "s/=/='/" -e "s/\$/'/" -e 's/^/export /' \
  > "$tmp" && mv "$tmp" "$file"
