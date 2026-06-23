import subprocess  # noqa: INP001
from enum import StrEnum
from pathlib import Path
from typing import cast

import git
import typer

MASTER_BRANCH = "master"
HAS_SUBMOD = True

app = typer.Typer()

REPO = git.Repo(".")
# assert not REPO.index.unmerged_blobs()


assert "origin" in REPO.remotes
ORIGIN = REPO.remotes["origin"]

assert MASTER_BRANCH in ORIGIN.refs
MASTER = ORIGIN.refs[MASTER_BRANCH]

MY = REPO.active_branch
assert MY.name != MASTER_BRANCH


def get_user_email() -> str:
    return cast("str", REPO.config_reader().get_value("user", "email", default=None))


def find_base(b0: git.Head = MY, b1: git.Head = MASTER) -> git.Commit:
    bases = REPO.merge_base(b0, b1)
    # assert len(bases) == 1
    return bases[0]


def is_dirty() -> bool:
    return REPO.is_dirty(untracked_files=True)


def is_rebasing() -> bool:
    rebase_merge_dir = Path(REPO.git_dir) / "rebase-merge"
    rebase_apply_dir = Path(REPO.git_dir) / "rebase-apply"
    return rebase_merge_dir.exists() or rebase_apply_dir.exists()


def count_commits(c0: git.Commit, c1: git.Commit) -> int:
    return len(list(REPO.iter_commits(f"{c0}..{c1}")))


def find_my_1st_commit_after_base(base: git.Commit) -> git.Commit:
    return REPO.commit(REPO.git.rev_list(f"{base}..{MY}", reverse=True, max_count="1").strip())


class Cmd(StrEnum):
    # fmt: off
    COMMIT     = "💾 Commit"
    PULL       = "🔽 Pull-FF"
    PUSH       = "🔼 Push"
    RESET      = "🪓 Reset"
    FORCE_PUSH = "⏫ Force Push"
    SQUASH     = "🔨 Squash"
    ABORT      = "🛑 Abort"
    REBASE     = "🌳 Rebase"
    MERGE      = "🔀 Merge"
    FETCH      = "⏬ Fetch"
    SYNC       = "🔄️ Sync"
    BRANCH     = "📂 Branch"
    TAG        = "🏷️ Tag"
    STASH_PUSH = "🗃️ Stash Push"
    STASH_POP  = "🗃️ Stash Pop"
    SUBMOD     = "📦 SubMod"
    MR         = "🙋🏼‍♂️ Merge Request"
    TIDY       = "🧹 Tidy"
    ENV        = "🌏 Env"
    # fmt: on

    def start(self):
        typer.secho(f"{self} START", fg=typer.colors.BLUE)

    def end(self):
        typer.secho(f"{self} END", fg=typer.colors.GREEN)

    def cancel(self):
        typer.secho(f"{self} CANCELLED", fg=typer.colors.YELLOW)

    def info(self, msg: str):
        typer.secho(f"{self}: {msg}", fg=typer.colors.BLUE)

    def warn(self, msg: str):
        typer.secho(f"🚨 {msg}", fg=typer.colors.YELLOW)

    def fail(self):
        typer.secho(f"{self} FAILED", fg=typer.colors.RED)

    def confirm(self, msg: str) -> bool:
        s = typer.style(f"✅ {msg}", fg=typer.colors.BLUE)
        return typer.confirm(s)


@app.command()
def show():
    for cmd in Cmd:
        print(cmd)


def commit(msg: str) -> None:
    if not is_dirty():
        return

    cmd = Cmd.COMMIT
    cmd.start()

    cmd.info(f"Commit with message: {msg}")
    REPO.git.add(A=True)
    REPO.index.commit(msg)

    cmd.end()


def pull() -> None:
    cmd = Cmd.PULL
    cmd.start()
    ORIGIN.pull()
    # ORIGIN.pull(ff_only=True)
    cmd.end()


def push() -> None:
    cmd = Cmd.PUSH
    cmd.start()
    ORIGIN.push(MY.name)
    cmd.end()


@app.command()
def force_push() -> None:
    cmd = Cmd.FORCE_PUSH
    cmd.start()

    try:
        ORIGIN.push(MY.name, force_with_lease=True)
    except git.GitCommandError:
        if (
            cmd.confirm("Someone committed into your origin, OVERWRITE his code?")
            and cmd.confirm("His code may be useful, continue?")
            and cmd.confirm("Are you sure?")
        ):
            ORIGIN.push(MY.name, force=True)
        else:
            cmd.cancel()
            return

    cmd.end()


def reset_to(
    c: git.Commit | git.Reference | None = None, *, is_soft: bool = False, need_commit: bool, need_push: bool
) -> None:
    if c is None:
        c = find_base()

    if MY.commit == c:
        return

    cmd = Cmd.RESET
    cmd.start()
    if is_soft:
        REPO.git.reset(c, soft=True)
    else:
        REPO.git.reset(c, mixed=True)
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
def squash() -> None:
    need_stash = is_dirty()

    cmd = Cmd.SQUASH
    cmd.start()

    if need_stash:
        stash_push()
        reset_to(need_commit=True, need_push=True)
        stash_pop()
    else:
        reset_to(need_commit=True, need_push=True)

    cmd.end()


def abort() -> None:
    # if not is_rebasing():
    #     return

    cmd = Cmd.ABORT
    cmd.start()
    REPO.git.rebase(abort=True)
    cmd.end()


def rebase_to(c: git.Commit) -> bool:
    cmd = Cmd.REBASE
    cmd.start()

    try:
        REPO.git.rebase(c, autostash=True)
    except git.GitCommandError:
        cmd.fail()
        return False
    else:
        cmd.end()

        force_push()
        submod()
        env()

        return True


def add_tag(name: str):
    cmd = Cmd.TAG
    cmd.start()
    REPO.create_tag(name, force=True)
    cmd.end()


def create_branch(name: str):
    cmd = Cmd.BRANCH
    cmd.start()
    REPO.create_head(name, force=True)
    cmd.end()


def rebase_and_retry(c: git.Commit, base: git.Commit) -> bool:
    cmd = Cmd.REBASE

    if rebase_to(c):
        return True
    abort()

    if not cmd.confirm(f"Found 💣 conflicts. Do you want to {Cmd.RESET} and {Cmd.REBASE}?"):
        cmd.cancel()
        return False

    add_tag(f"{MY.name}-backup-{base.hexsha[:8]}")
    merge()

    # reset_to(base, need_commit=True, need_push=False)
    # submod()
    # if not rebase_to(c):
    #     ORIGIN.push(MY.name, delete=True)
    #     cmd.warn(f"Please resolve 💣 conflicts manually, then {Cmd.REBASE}")
    #     return False
    return True


def fetch() -> None:
    cmd = Cmd.FETCH
    cmd.start()
    ORIGIN.fetch(prune=True, tags=True, prune_tags=False, recurse_submodules=True)
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
            cmd.warn(f"You need to {Cmd.FORCE_PUSH} manually")
            cmd.cancel()
            return False

        if cmd.confirm(f"{Cmd.PULL} to your origin branch?"):
            # if not rebase_and_retry(my_origin.commit, find_base(MY, my_origin)):
            #     cmd.cancel()
            #     return False
            pull()
        else:
            cmd.warn(f"You need to choose {Cmd.FORCE_PUSH} or {Cmd.REBASE}")
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

    cmd = Cmd.REBASE

    base = find_base()
    if base == MASTER.commit:
        submod()
        env()
        push()
    else:
        email = find_my_1st_commit_after_base(base).author.email
        if get_user_email() != email:
            cmd.warn(f"You are not {email}, should not {Cmd.REBASE} other's branch")
            cmd.cancel()
            return

        rebase_and_retry(MASTER.commit, base)


@app.command()
def merge(need_sync: bool = True) -> None:  # noqa: FBT001, FBT002
    if need_sync and not sync():
        return

    cmd = Cmd.MERGE
    cmd.start()
    REPO.git.merge(MASTER)
    cmd.end()

    submod()
    env()
    push()


@app.command()
def tidy() -> None:
    cmd = Cmd.TIDY
    cmd.start()
    merge()
    reset_to(MASTER, need_commit=False, need_push=True)
    cmd.end()


@app.command()
def mr() -> None:
    merge()
    reset_to(MASTER, need_commit=False, need_push=True)


def stash_push() -> None:
    cmd = Cmd.STASH_PUSH
    cmd.start()
    REPO.git.stash("push", "--include-untracked")
    cmd.end()


def stash_pop() -> None:
    cmd = Cmd.STASH_POP
    cmd.start()
    REPO.git.stash("pop", "--index")
    cmd.end()


@app.command()
def submod() -> None:
    if not HAS_SUBMOD:
        return

    cmd = Cmd.SUBMOD
    cmd.start()
    REPO.git.submodule("update", "--init", "--recursive", "--force")
    cmd.end()


@app.command()
def env() -> None:
    cmd = Cmd.ENV
    cmd.start()
    subprocess.run(["uv", "sync"], check=True)  # noqa: S607
    cmd.end()


@app.command()
def test():
    email = find_my_1st_commit_after_base(find_base()).author.email
    print(email)


@app.command()
def zen() -> None:
    z = [
        "始终保持树形结构, 线性历史",
        "只有 3 个分支: 你的分支, 你的远程分支和主分支",
        "自己的分支自己负责",
    ]
    for line in z:
        typer.echo(line)


if __name__ == "__main__":
    app()
