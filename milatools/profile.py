import json
import re
from pathlib import Path

import invoke
import questionary as qn

style = qn.Style(
    [
        ("envname", "yellow bold"),
        ("envpath", "cyan"),
        ("prefix", "bold"),
        ("special", "orange bold"),
        ("cancel", "grey bold"),
    ]
)


def _ask_name(message, default=""):
    while True:
        name = qn.text(message, default=default).ask()
        if re.match(r"[a-zA-Z0-9_]+", name):
            return name
        else:
            qn.print(f"Invalid name: {name}", style="bold red")


def setup_profile(remote, path):
    profile = select_preferred(remote, path)
    preferred = profile is not None
    if not preferred:
        profile = select_profile(remote)
    if profile is None:
        profile = create_profile(remote)

    profile_file = Path(path) / ".milatools-profile"
    if not preferred:
        save = qn.confirm(
            f"Do you want to use this profile by default in {path}?"
        ).ask()
        if save:
            remote.puttext(profile, str(profile_file))

    return profile


def select_preferred(remote, path):
    preferred = f"{path}/.milatools-profile"
    qn.print(f"Checking for preferred profile in {preferred}...")

    try:
        preferred = remote.get(f"cat {preferred}", hide=True, display=False)
    except invoke.exceptions.UnexpectedExit:
        print("None found.")
        preferred = None

    return preferred


def select_profile(remote):
    profdir = ".milatools/profiles"

    qn.print(f"Fetching profiles in {profdir}...")

    try:
        profiles = remote.get(f"ls {profdir}/*.bash", hide=True, display=False).split()
    except invoke.exceptions.UnexpectedExit:
        profiles = []

    if not profiles:
        print("None found.")
        return None, False

    profile_choices = [
        qn.Choice(
            title=Path(p).stem,
            value=p,
        )
        for p in profiles
    ]

    profile = qn.select(
        "Select the profile to use:",
        choices=[
            *profile_choices,
            qn.Choice(
                title=[("class:special", "Create a new profile")], value="<CREATE>"
            ),
        ],
    ).ask()

    if profile == "<CREATE>":
        return None

    return profile


def create_profile(remote, path="~"):
    modules = select_modules(remote)

    mload = f"module load {' '.join(modules)}"
    lines = [mload]

    default_profname = ""
    if any("conda" in m for m in modules):
        env = select_conda_environment(remote, loader=mload)
        lines.append(f"conda activate {env}")
        default_profname = _env_basename(env)

    elif any("python" in m for m in modules):
        vpath = select_virtual_environment(remote, path, loader=mload)
        lines.append(f"source {vpath}/bin/activate")

    profname = _ask_name("Name of the profile:", default=default_profname)
    profcontents = "\n".join(lines)
    prof_file = f".milatools/profiles/{profname}.bash"
    qn.print(f"Saving to {prof_file}", style="bold cyan")
    qn.print("==========")
    qn.print(profcontents)
    qn.print("==========")
    remote.puttext(f"{profcontents}\n", prof_file)

    return prof_file


def select_modules(remote):
    choices = [
        qn.Choice(
            title="miniconda/3",
            value="miniconda/3 cuda/11.2/cudnn/8.1",
        ),
        qn.Choice(
            title="python/3.8",
            value="python/3.8 cuda/11.2/cudnn/8.1",
        ),
        qn.Choice(
            title="pytorch/1.8.1",
            value="python/3.7 python/3.7/cuda/11.1/cudnn/8.0/pytorch/1.8.1",
        ),
        qn.Choice(
            title=[("class:special", "Other (specify)")],
            value="<OTHER>",
        ),
    ]
    modules = qn.select(
        "Select the set of modules to load:",
        choices=choices,
    ).ask()
    if modules == "<OTHER>":
        qn.print("Fetching the list of modules...")
        # "module --terse avail" prints on stderr? Really?!
        modlist = (
            remote.run("module --terse avail", display=False, hide=True)
            .stderr.strip()
            .split()
        )
        modchoices = {
            x.split("(@")[0]: x.split("(@")[-1].rstrip(")")
            for x in modlist
            if not x.endswith(":")
        }
        qn.print(
            "Write one module on each line, press enter on an empty line to finish",
            style="bold",
        )
        modules = []
        while True:
            entry = (
                qn.autocomplete(
                    "",
                    choices=modchoices.keys(),
                    style=qn.Style([("answer", "fg:default bg:default")]),
                )
                .ask()
                .strip()
            )
            if not entry:
                break
            if entry not in modchoices:
                qn.print(f"{entry} is not a valid module", style="bold red")
                continue
            modules.append(modchoices[entry])
    else:
        modules = modules.split()

    return modules


def _env_basename(pth):
    base = pth.split("/")[-1]
    if base == "3":
        return None
    return base


def select_conda_environment(remote, loader="module load miniconda/3"):
    qn.print("Fetching the list of conda environments...")
    envstr = remote.get(
        f"conda env list --json", hide=True, display=False, precommand=loader
    )
    envlist = json.loads(envstr)["envs"]

    choices = [
        qn.Choice(
            title=[
                ("class:envname", f"{_env_basename(entry):15}"),
                ("class:envpath", f" {entry}"),
            ],
            value=entry,
        )
        for entry in envlist
        if _env_basename(entry)
    ]

    choices.extend(
        [
            qn.Choice(
                title=[("class:special", "Other (specify)")],
                value="<OTHER>",
            ),
            qn.Choice(
                title=[("class:special", "Create a new environment")],
                value="<CREATE>",
            ),
        ]
    )

    env = qn.select(
        "Select the environment to use:", choices=choices, style=style
    ).ask()

    if env == "<OTHER>":
        env = qn.text("Enter the path to the environment to use.").ask()

    elif env == "<CREATE>":
        pyver = qn.select(
            "Choose the Python version",
            choices=["3.10", "3.9", "3.8", "3.7"],
        ).ask()
        envname = _ask_name("What should the environment name be?")
        remote.run(
            f"srun conda create -y -n {envname} python={pyver}",
            precommand=loader,
        )
        env = envname

    return env


def select_virtual_environment(remote, path, loader="module load python"):
    envstr = remote.get(
        f"ls -d {path}/venv {path}/.venv {path}/virtualenv ~/virtualenvs/*",
        warn=True,
        precommand=loader,
    )
    choices = [x for x in envstr.split() if x]
    choices.extend(
        [
            qn.Choice(
                title=[("class:special", "Other (specify)")],
                value="<OTHER>",
            ),
            qn.Choice(
                title=[("class:special", "Create a new environment")],
                value="<CREATE>",
            ),
        ]
    )

    env = qn.select(
        "Select the environment to use:", choices=choices, style=style
    ).ask()

    if env == "<OTHER>":
        env = qn.text("Enter the path to the environment to use.").ask()

    elif env == "<CREATE>":
        envname = _ask_name("What should the environment name be?")
        env = f"~/virtualenvs/{envname}"
        remote.run(
            f"srun python -m venv {env}",
            precommand=loader,
        )

    return env
