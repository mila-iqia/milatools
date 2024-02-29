from __future__ import annotations

import json
import re
import shlex
import typing
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Generic, TypeVar

import invoke
import questionary as qn
from questionary import Style
from questionary.prompts.common import FormattedText

from .utils import askpath, yn

if typing.TYPE_CHECKING:
    from milatools.cli.remote import Remote

style = qn.Style(
    [
        ("envname", "yellow bold"),
        ("envpath", "cyan"),
        ("prefix", "bold"),
        ("special", "orange bold"),
        ("cancel", "grey bold"),
    ]
)


def _ask_name(message: str, default: str = "") -> str:
    while True:
        name: str = qn.text(message, default=default).unsafe_ask()
        if re.match(r"[a-zA-Z0-9_]+", name):
            return name
        else:
            qn.print(f"Invalid name: {name}", style="bold red")


def setup_profile(remote: Remote, path: str) -> str:
    profile = select_preferred(remote, path)
    preferred = profile is not None
    if not preferred:
        profile = select_profile(remote)
    if profile is None:
        profile = create_profile(remote)

    profile_file = PurePosixPath(path) / ".milatools-profile"
    if not preferred:
        save = yn(
            f"Do you want to use this profile by default in {path}?",
            default=False,
        )
        if save:
            remote.puttext(profile, str(profile_file))

    return profile


def select_preferred(remote: Remote, path: str) -> str | None:
    preferred = f"{path}/.milatools-profile"
    qn.print(f"Checking for preferred profile in {preferred}")

    try:
        preferred = remote.get_output(f"cat {preferred}", hide=True)
    except invoke.exceptions.UnexpectedExit:
        qn.print("None found.", style="grey")
        preferred = None

    return preferred


def select_profile(remote: Remote) -> str | None:
    profdir = "~/.milatools/profiles"

    qn.print(f"Fetching profiles in {profdir}")

    profiles = remote.get_lines(f"ls {profdir}/*.bash", hide=True, warn=True)

    if not profiles:
        qn.print("None found.", style="grey")
        qn.print("Creating a new profile.")
        return None

    profile_choices = [
        Choice(
            title=Path(p).stem,
            value=p,
        )
        for p in profiles
    ]

    profile = _select_unsafe_ask(
        "Select the profile to use:",
        choices=[
            *profile_choices,
            Choice(
                title=[("class:special", "Create a new profile")],
                value="<CREATE>",
            ),
        ],
        style=style,
    )

    if profile == "<CREATE>":
        return None

    return profile


def create_profile(remote: Remote, path: str = "~"):
    modules = select_modules(remote)

    mload = f"module load {' '.join(modules)}"
    lines = [mload]

    default_profname = ""
    if any("conda" in m for m in modules):
        env = select_conda_environment(remote.with_precommand(mload))
        lines.append(f"conda activate {env}")
        default_profname = _env_basename(env)

    elif any("python" in m for m in modules):
        vpath = select_virtual_environment(remote.with_precommand(mload), path)
        lines.append(f"source {vpath}/bin/activate")
        default_profname = _env_basename(vpath)

    assert default_profname is not None
    profname = _ask_name("Name of the profile:", default=default_profname)
    profcontents = "\n".join(lines)
    prof_file = f".milatools/profiles/{profname}.bash"
    qn.print(f"Saving to {prof_file}", style="bold cyan")
    qn.print("==========")
    qn.print(profcontents)
    qn.print("==========")
    remote.puttext(f"{profcontents}\n", prof_file)

    return prof_file


def select_modules(remote: Remote):
    choices = [
        Choice(
            title="miniconda/3",
            value="miniconda/3 cuda/11.2/cudnn/8.1",
        ),
        Choice(
            title="python/3.8",
            value="python/3.8 cuda/11.2/cudnn/8.1",
        ),
        Choice(
            title="pytorch/1.8.1",
            value="python/3.7 python/3.7/cuda/11.1/cudnn/8.0/pytorch/1.8.1",
        ),
        Choice(
            title=[("class:special", "Other (specify)")],
            value="<OTHER>",
        ),
    ]
    modules = _select_unsafe_ask(
        "Select the set of modules to load:",
        choices=choices,
    )

    if modules != "<OTHER>":
        return modules.split()

    qn.print("Fetching the list of modules...")
    # "module --terse avail" prints on stderr? Really?!
    modlist = remote.run("module --terse avail", hide=True).stderr.strip().split()
    modchoices = {
        x.split("(@")[0]: x.split("(@")[-1].rstrip(")")
        for x in modlist
        if not x.endswith(":")
    }
    qn.print(
        "Write one module on each line, press enter on an empty line to finish",
        style="bold",
    )
    module_list: list[str] = []
    while True:
        entry: str = qn.autocomplete(
            "",
            choices=list(modchoices.keys()),
            style=qn.Style([("answer", "fg:default bg:default")]),
        ).unsafe_ask()
        entry = entry.strip()
        if not entry:
            break
        if entry not in modchoices:
            qn.print(f"{entry} is not a valid module", style="bold red")
            continue
        module_list.append(modchoices[entry])
    return module_list


def _env_basename(pth: str) -> str | None:
    base = pth.split("/")[-1]
    if base == "3":
        return None
    return base


def select_conda_environment(remote: Remote, loader: str = "module load miniconda/3"):
    qn.print("Fetching the list of conda environments...")
    envstr = remote.get_output("conda env list --json", hide=True)
    envlist: list[str] = json.loads(envstr)["envs"]
    choices: list[Choice[str]] = [
        Choice(
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
            Choice(
                title=[("class:special", "Other (specify)")],
                value="<OTHER>",
            ),
            Choice(
                title=[("class:special", "Create a new environment")],
                value="<CREATE>",
            ),
        ]
    )

    env = _select_unsafe_ask(
        "Select the environment to use:", choices=choices, style=style
    )

    if env == "<OTHER>":
        return askpath("Enter the path to the environment to use.", remote)

    if env == "<CREATE>":
        pyver = _select_unsafe_ask(
            "Choose the Python version",
            choices=["3.10", "3.9", "3.8", "3.7"],
        )
        env = _ask_name("What should the environment name/path be?")
        if "/" not in env:
            # env = f"~/scratch/condaenvs/{env}"
            env = f"~/condaenvs/{env}"
        remote.run(
            f"srun conda create -y --prefix {env} python={pyver}",
        )

    return env


def select_virtual_environment(remote: Remote, path):
    envstr = remote.get_output(
        (
            f"ls -d {path}/venv {path}/.venv {path}/virtualenv ~/virtualenvs/* "
            "~/scratch/virtualenvs/*"
        ),
        hide=True,
        warn=True,
    )
    choices: list[str | Choice[str]] = [x for x in envstr.split() if x]
    choices.extend(
        [
            Choice(
                title=[("class:special", "Other (specify)")],
                value="<OTHER>",
            ),
            Choice(
                title=[("class:special", "Create a new environment")],
                value="<CREATE>",
            ),
        ]
    )

    env = _select_unsafe_ask(
        "Select the environment to use:", choices=choices, style=style
    )
    if env == "<OTHER>":
        env = askpath("Enter the path to the environment to use.", remote)

    elif env == "<CREATE>":
        env = _ask_name("What should the environment name/path be?")
        if "/" not in env:
            # env = f"~/scratch/virtualenvs/{env}"
            env = f"~/virtualenvs/{env}"
        remote.run(f"srun python -m venv {env}")

    return env


def ensure_program(remote: Remote, program: str, installers: dict[str, str]):
    to_test = [program, *installers.keys()]
    progs = [
        Path(p).name
        for p in remote.get_output(
            shlex.join(["which", *to_test]),
            hide=True,
            warn=True,
        ).split()
    ]

    if program not in progs:
        choices: list[str | Choice[str]] = [
            *[cmd for prog, cmd in installers.items() if prog in progs],
            Choice(title="I will install it myself.", value="<MYSELF>"),
        ]
        install = _select_unsafe_ask(
            (
                f"{program} is not installed in that environment. "
                f"Do you want to install it?"
            ),
            choices=choices,
        )
        if install == "<MYSELF>":
            return False
        else:
            remote.run(f"srun {install}")

    return True


_T = TypeVar("_T")


# NOTE: This here is a small typing improvement over the `qn.Choice` class: this marks
# it as a generic class. Functions that take in a `Choice[_T]` can then mark their
# return type based on _T, as is done below.
class Choice(qn.Choice, Generic[_T]):
    value: _T

    def __init__(
        self,
        title: FormattedText,
        value: _T | None = None,
        disabled: str | None = None,
        checked: bool | None = False,
        shortcut_key: str | bool | None = True,
    ) -> None:
        super().__init__(
            title=title,
            value=value,
            disabled=disabled,
            checked=checked,
            shortcut_key=shortcut_key,
        )


@typing.overload
def _select_unsafe_ask(
    message: str,
    choices: Sequence[str | Choice[str]],
    style: Style | None = None,
) -> str:
    ...


@typing.overload
def _select_unsafe_ask(
    message: str,
    choices: Sequence[Choice[_T] | dict[str, _T]],
    style: Style | None = None,
) -> _T:
    ...


def _select_unsafe_ask(
    message: str,
    choices: Sequence[str | Choice[_T] | dict[str, _T]],
    style: Style | None = None,
) -> _T | str:
    """Small helper function that does `qn.select` followed by `qn.unsafe_ask`.

    This has the benefit that the output type of this function is known based on the
    type of inputs passed.
    """
    question = qn.select(message, choices, style=style)
    value = question.unsafe_ask()
    return value
