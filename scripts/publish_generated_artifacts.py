#!/usr/bin/env python3
"""Publish generated paths without rebasing binary artifacts.

Actions jobs can start from the same source commit and finish in a different
order. Rebasing a commit that contains a generated PDF is inherently fragile:
Git cannot merge two versions of the binary. This helper keeps generated paths
aside, refreshes the worktree from the current remote branch, restores only
those paths, and creates a fresh commit on top of that branch.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--branch", default=os.environ.get("GITHUB_REF_NAME", "main"))
    parser.add_argument("--message", required=True)
    parser.add_argument("--attempts", type=int, default=4)
    parser.add_argument("paths", nargs="+")
    return parser.parse_args()


def validated_paths(values: list[str]) -> list[Path]:
    paths: list[Path] = []
    for value in values:
        path = (ROOT / value).resolve()
        if ROOT not in path.parents or not path.exists():
            raise SystemExit(f"Generated path is missing or outside the repository: {value}")
        paths.append(path)
    return paths


def copy_path(source: Path, target: Path) -> None:
    if source.is_dir():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def main() -> None:
    args = parse_args()
    if args.attempts < 1:
        raise SystemExit("--attempts must be at least 1")

    paths = validated_paths(args.paths)
    relative = [path.relative_to(ROOT) for path in paths]

    git("config", "user.name", "github-actions[bot]")
    git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")

    with tempfile.TemporaryDirectory(prefix="book-artifacts-") as temp_name:
        temp = Path(temp_name)
        for source, rel in zip(paths, relative):
            copy_path(source, temp / rel)

        for attempt in range(1, args.attempts + 1):
            fetch = git("fetch", "origin", args.branch, check=False)
            if fetch.returncode:
                print(fetch.stdout, end="")
                if attempt == args.attempts:
                    raise SystemExit("Could not refresh the publication branch")
                time.sleep(attempt * 2)
                continue

            # Generated outputs are authoritative only for the listed paths.
            # Starting from the remote tip preserves every unrelated change and
            # avoids trying to merge binary PDFs or regenerated checksums.
            git("reset", "--hard", f"origin/{args.branch}")
            for rel in relative:
                target = ROOT / rel
                remove_path(target)
                copy_path(temp / rel, target)

            git("add", "-A", "--", *(str(path) for path in relative))
            if git("diff", "--cached", "--quiet", check=False).returncode == 0:
                print("Generated artifacts are unchanged.")
                return

            git("commit", "-m", args.message)
            push = git("push", "origin", f"HEAD:{args.branch}", check=False)
            print(push.stdout, end="")
            if push.returncode == 0:
                print(f"Published generated artifacts on attempt {attempt}.")
                return

            print(
                f"::notice::The publication branch advanced during push; "
                f"refreshing generated artifacts ({attempt}/{args.attempts})."
            )
            time.sleep(attempt * 2)

    raise SystemExit("Failed to publish generated artifacts after retries")


if __name__ == "__main__":
    main()
