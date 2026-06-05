"""Command-line interface for git-fat."""

from __future__ import annotations

import sys

from git_fat.core import GitFat

COMMANDS = {
    "filter-clean": lambda fat, args: fat.cmd_filter_clean(),
    "filter-smudge": lambda fat, args: fat.cmd_filter_smudge(),
    "init": lambda fat, args: fat.cmd_init(),
    "status": lambda fat, args: fat.cmd_status(args),
    "push": lambda fat, args: fat.cmd_push(args),
    "pull": lambda fat, args: fat.cmd_pull(args),
    "gc": lambda fat, args: fat.cmd_gc(),
    "verify": lambda fat, args: fat.cmd_verify(),
    "checkout": lambda fat, args: fat.cmd_checkout(args),
    "find": lambda fat, args: fat.cmd_find(args),
    "index-filter": lambda fat, args: fat.cmd_index_filter(args),
}


def main() -> None:
    fat = GitFat()
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    handler = COMMANDS.get(cmd)
    if handler:
        handler(fat, sys.argv[2:])
    else:
        print(
            "Usage: git fat [init|status|push|pull|gc|verify|checkout|find|index-filter]",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
