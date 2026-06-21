import os  # noqa: INP001
import subprocess
import time
from enum import StrEnum
from pathlib import Path
from shutil import rmtree
from typing import Annotated

import git
import typer

MASTER_BRANCH = "master"

app = typer.Typer()

REPO = git.Repo(".")
assert REPO.index.unmerged_blobs() == {}
# assert repo.git.stash("list") == ""

assert "origin" in REPO.remotes
ORIGIN = REPO.remotes["origin"]

if MASTER_BRANCH not in ORIGIN.refs:
    raise ValueError
MASTER = ORIGIN.refs[MASTER_BRANCH]

MY = REPO.active_branch
assert MY.name != MASTER_BRANCH

# config_reader = REPO.config_reader()
# USER_NAME = cast("str", config_reader.get_value("user", "name", default=None))
# USER_EMAIL = cast("str", config_reader.get_value("user", "email", default=None))


def find_base(b0: git.Head = MY, b1: git.Head = MASTER):
    bases = REPO.merge_base(b0, b1)
    # assert len(bases) == 1
    return bases[0]


def is_dirty() -> bool:
    return REPO.is_dirty(untracked_files=True)


def count_commits(c0: git.Commit, c1: git.Commit):
    return len(list(REPO.iter_commits(f"{c0}..{c1}")))


class Cmd(StrEnum):
    # fmt: off
    COMMIT     = "💾 Commit"
    PULL       = "⬇️  Pull"
    PUSH       = "⬆️  Push"
    RESET      = "🪓 Reset"
    FORCE_PUSH = "⏫ Force Push"
    SQUASH     = "🔨 Squash"
    ABORT      = "🛑 Abort"
    REBASE     = "🌳 Rebase"
    MERGE      = "🔀 Merge"
    FETCH      = "⏬ Fetch"
    SYNC       = "🔃 Sync"
    STASH_PUSH = "🗄️  Stash Push"
    STASH_POP  = "🗄️  Stash Pop"
    SUBMOD     = "📦 SubMod"
    SCOOP      = "🥄 Scoop"
    ENV        = "🌏 Env"
    CLEAN      = "🗑️  Clean"
    CHECK      = "🚓 Check"
    # fmt: on

    def start(self):
        typer.secho(f"{self} START", fg=typer.colors.GREEN)

    def end(self):
        typer.secho(f"{self} END", fg=typer.colors.GREEN)

    def cancel(self):
        typer.secho(f"{self} CANCELLED", fg=typer.colors.YELLOW)

    def fail(self, e: Exception):
        typer.echo(e)
        typer.secho(f"{self} FAILED", fg=typer.colors.RED)

    def info(self, msg: str):
        typer.secho(f"{self}: {msg}", fg=typer.colors.BLUE)

    def warn(self, msg: str):
        typer.secho(f"🚨 {msg}", fg=typer.colors.YELLOW)

    def error(self, msg: str):
        return RuntimeError(f"💥 {msg}")

    def confirm(self, msg: str) -> bool:
        s = typer.style(f"✅ {msg}", fg=typer.colors.BLUE)
        return typer.confirm(s)

    def run(self, cmds: str | list[str], *, use_proxy: bool = False):
        if use_proxy:
            proxy = "http://10.3.6.15:3128"
            env = os.environ.copy()
            env["HTTP_PROXY"] = proxy
            env["HTTPS_PROXY"] = proxy
        else:
            env = None

        if isinstance(cmds, str):
            cmds = [cmds]
        for c in cmds:
            self.info(f"{c}")
            subprocess.run(c, check=True, shell=True, capture_output=False, text=True, env=env)  # noqa: S602


@app.command()
def show():
    for cmd in Cmd:
        cmd.start()


@app.command()
def commit(msg: Annotated[str, typer.Argument()] = "update") -> None:
    if not is_dirty():
        return

    cmd = Cmd.COMMIT
    cmd.start()

    cmd.info(f"{msg}")
    if not REPO.index.diff("HEAD"):
        REPO.git.add(A=True)
    REPO.index.commit(msg)

    cmd.end()


def pull() -> None:
    cmd = Cmd.PULL
    cmd.start()
    ORIGIN.pull(rebase=True, autostash=True)
    cmd.end()


def push() -> None:
    cmd = Cmd.PUSH
    cmd.start()
    ORIGIN.push(MY.name)
    cmd.end()


def reset_to(c: git.Commit | None = None, *, is_soft: bool, need_commit: bool, need_push: bool) -> None:
    if c is None:
        c = find_base()

    if MY.commit == c:
        return

    cmd = Cmd.RESET
    cmd.start()
    if is_soft:
        REPO.git.reset(c, soft=True)
    else:
        REPO.git.reset(c)
    cmd.end()

    if need_commit:
        msg = f"reset to {c.hexsha[:8]}"
        commit(msg)

    if need_push:
        force_push()


@app.command()
def reset():
    reset_to(is_soft=True, need_commit=False, need_push=True)


@app.command()
def force_push() -> bool:
    cmd = Cmd.FORCE_PUSH
    cmd.start()

    try:
        ORIGIN.push(MY.name, force_with_lease=True)
    except git.GitCommandError as e:
        cmd.fail(e)
        if (
            cmd.confirm("Someone commited into your-origin, OVERWRITE his code?")
            and cmd.confirm("His code may be usefull, continue?")
            and cmd.confirm("Are you sure?")
        ):
            ORIGIN.push(MY.name, force=True)
            # raise cmd.error("You can input: git push --force") from e
        cmd.cancel()
        return False

    cmd.end()
    return True


@app.command()
def squash() -> None:
    cmd = Cmd.SQUASH
    cmd.start()
    msg = stash_push()
    reset_to(is_soft=False, need_commit=True, need_push=True)
    stash_pop(msg)
    cmd.end()


@app.command()
def abort() -> None:
    rebase_merge_dir = Path(REPO.git_dir) / "rebase-merge"
    rebase_apply_dir = Path(REPO.git_dir) / "rebase-apply"

    is_rebasing = rebase_merge_dir.exists() or rebase_apply_dir.exists()

    if not is_rebasing:
        return

    cmd = Cmd.ABORT
    cmd.start()

    try:
        REPO.git.rebase(abort=True)
    except git.GitCommandError as e:
        cmd.fail(e)
        raise cmd.error("You need to find help") from e

    cmd.end()


def rebase_to(c: git.Commit) -> bool:
    cmd = Cmd.REBASE
    cmd.start()

    try:
        REPO.git.rebase(c, autostash=True)
    except git.GitCommandError as e:
        cmd.fail(e)
        return False
    else:
        force_push()
        submod()
        env()
        cmd.end()
        return True

def reset_and_rebase(c: git.Commit, base: git.Commit) -> bool:
    reset_to(base, is_soft=False, need_commit=True, need_push=False)
    return rebase_to(c)

def merge():
    cmd = Cmd.MERGE
    cmd.start()
    try:
        MY.merge(MASTER)
    except git.GitCommandError as e:
        cmd.fail(e)
        return False
    else:
        push()
        cmd.end()
        return True


def rebase_or_conflict(c: git.Commit, base: git.Commit) -> bool:
    cmd = Cmd.REBASE

    if rebase_to(c):
        return True
    abort()

    if not cmd.confirm(f"Found 💣 conflicts. Do you want to {Cmd.RESET} and {Cmd.REBASE}?"):
        cmd.cancel()
        return False

    submod()
    reset_to(base, is_soft=False, need_commit=True, need_push=False)

    if not rebase_to(c):
        ORIGIN.push(MY.name, delete=True)
        cmd.warn(f"Please resolve 💣 conflicts manually, then {Cmd.REBASE}")
        return False
    return True



def fetch() -> None:
    cmd = Cmd.FETCH
    cmd.start()
    ORIGIN.fetch(prune=True, tags=True, prune_tags=True)
    cmd.end()


@app.command()
def sync() -> bool:
    fetch()

    cmd = Cmd.SYNC
    cmd.start()

    if MY.name not in ORIGIN.refs:
        cmd.warn("Your branch is not in origin")
        cmd.cancel()
        return True

    base = find_base()

    if base != MASTER.commit:
        cmd.warn(f"Your branch is out of date, need to {Cmd.REBASE}")

    my_origin = ORIGIN.refs[MY.name]

    my_ahead = count_commits(my_origin.commit, MY.commit)
    my_origin_ahead = count_commits(MY.commit, my_origin.commit)

    if my_ahead > 0 and my_origin_ahead == 0:
        cmd.info(f"{Cmd.PUSH} your branch")
        push()
    elif my_ahead == 0 and my_origin_ahead > 0:
        cmd.info(f"{Cmd.PULL} your origin branch")
        pull()
    elif my_ahead > 0 and my_origin_ahead > 0:
        cmd.warn("Found 🍴 Fork")

        if (base.committed_datetime > find_base(my_origin, MASTER).committed_datetime) and (
            cmd.confirm(f"{Cmd.FORCE_PUSH} your branch?")
        ):
            force_push()
        elif cmd.confirm(f"{Cmd.PULL} your origin branch?"):
            if not rebase_or_conflict(my_origin.commit, find_base(MY, my_origin)):
                cmd.cancel()
                return False
        else:
            cmd.warn(f"You need to choose {Cmd.FORCE_PUSH} or {Cmd.PULL}")
            cmd.cancel()
            return False
    else:
        cmd.info("Your origin branch is already up to date")

    cmd.end()
    return True


@app.command()
def rebase() -> None:
    if not sync():
        return

    base = find_base()
    if base == MASTER.commit:
        submod()
        env()
        push()
    else:
        rebase_or_conflict(MASTER.commit, base)


# @app.command()
# def merge() -> None:
#     if not sync():
#         return
#     MY.merge(MASTER)


def stash_push() -> str:
    msg = f"{time.time_ns()}"
    cmd = Cmd.STASH_PUSH
    cmd.start()
    REPO.git.stash("push", "--include-untracked", "-m", msg)
    cmd.end()
    return msg


def stash_pop(msg: str | None = None) -> None:
    cmd = Cmd.STASH_POP
    cmd.start()
    if msg is None:
        REPO.git.stash("pop", "--index")
    else:
        stash_list = REPO.git.stash("list").splitlines()
        target_stash = None
        for i, stash_entry in enumerate(stash_list):
            if msg in stash_entry:
                target_stash = i
                break
        if target_stash is not None:
            REPO.git.stash("pop", "--index", str(target_stash))
            cmd.end()
        else:
            cmd.cancel()


@app.command()
def submod() -> None:
    cmd = Cmd.SUBMOD
    cmd.start()
    args = ["update", "--init", "--recursive", "--force"]
    try:
        REPO.git.submodule(args)
    except git.GitCommandError as e:
        cmd.fail(e)
        raise cmd.error("You need to find help") from e
    cmd.end()


@app.command()
def zen() -> None:
    z = [
        "始终保持树形结构, 线性历史",
        "只有 3 个分支: 你的分支, 你的远程分支和主分支",
        "自己的分支自己负责",
    ]
    for line in z:
        typer.echo(line)


@app.command()
def scoop() -> None:
    cmd = Cmd.SCOOP
    cmd.start()
    cmd.run("powershell scripts/install_scoop.ps1")
    cmd.end()


@app.command()
def env() -> None:
    cmd = Cmd.ENV
    cmd.start()
    cmd.run("uv sync")
    cmd.end()


@app.command()
def clean() -> None:
    cmd = Cmd.CLEAN
    cmd.start()

    dirs = [Path("logs"), Path("output")]
    for d in dirs:
        if d.exists():
            rmtree(d)
        d.mkdir(parents=True, exist_ok=True)
        gitkeep = d / ".gitkeep"
        gitkeep.touch()

    force_dirs = [Path("MsCamRegLog")]
    for d in force_dirs:
        if d.exists():
            rmtree(d)

    cmd.end()


@app.command()
def check(dirs: Annotated[str, typer.Argument()] = "src tests", *, strict: bool = False) -> None:
    cmd = Cmd.CHECK
    cmd.start()

    try:
        cmd.run(f"uv run ruff format {dirs}")
        cmd.run(f"uv run ruff check {dirs} --fix --unsafe-fixes")
        cmd.run(f"uv run pyright {dirs}")
        if strict:
            cmd.run(f"uv run pylint {dirs}")
            cmd.run("uv run pytest --collect-only")
    except subprocess.CalledProcessError as e:
        cmd.fail(e)
        return
    cmd.end()


if __name__ == "__main__":
    app()
