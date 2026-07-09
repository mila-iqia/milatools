# Managed by milatools (`mila code`) -- do not edit this block.
# In SSH sessions adopted into a SLURM job by pam_slurm_adopt (e.g. VS Code
# Remote-SSH), only SLURM_JOB_ID is set. This restores the rest of the job's
# allocation environment, dumped by milatools when the job was created.
if [ -n "${SLURM_JOB_ID:-}${SLURM_JOBID:-}" ] \
   && [ -z "${SLURM_NTASKS:-}" ] && [ -z "${SLURM_STEP_ID:-}" ]; then
    _milatools_slurm_env="$HOME/.cache/milatools/slurm_env/${SLURM_JOB_ID:-${SLURM_JOBID:-}}.env"
    if [ -f "$_milatools_slurm_env" ]; then
        . "$_milatools_slurm_env"
        # Names of the variables injected above (+ the job id, set by
        # pam_slurm_adopt). Kept in a shell variable so the wrapper below
        # keeps working after the env file is removed (job closed / pruned).
        _milatools_slurm_env_vars="$(sed -n 's/^export \([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' "$_milatools_slurm_env" | tr '\n' ' ') SLURM_JOB_ID SLURM_JOBID"
        # `sbatch` from this session submits with a clean environment (as from
        # a login shell): with sbatch's default --export=ALL, the job's
        # variables restored above would otherwise leak into the submitted
        # job. `srun` is untouched: it needs them to run inside this job.
        # Use `\sbatch` to bypass the wrapper.
        # (`function` form: in zsh, a pre-existing `sbatch` alias would be a
        # parse error in the POSIX `sbatch() {` form.)
        function sbatch {
            (
                IFS=$' \t\n'
                # ($(printf ...) instead of plain $var: zsh doesn't word-split
                # parameter expansions, but does split command substitutions.)
                for _v in $(printf '%s' "${_milatools_slurm_env_vars:-}"); do
                    # Never unset cluster config (could come from an env file
                    # dumped by an older milatools).
                    case "$_v" in SLURM_CONF) continue ;; esac
                    unset "$_v"
                done
                command sbatch "$@"
            )
        }
    fi
    unset _milatools_slurm_env
fi
