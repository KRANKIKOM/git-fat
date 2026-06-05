# Installation and usage

git-fat runs in Docker. Build once, put the wrapper script on your `PATH`, and use it like the original tool.

## Build

```bash
docker build -t git-fat:latest .
```

## Install wrapper

Add the repository directory (or a symlink to `git-fat`) to your `PATH`:

```bash
export PATH="/path/to/git-fat:$PATH"
```

The `git-fat` shell script invokes Docker for every command, mounting the current git repository so filter hooks (`filter-clean` / `filter-smudge`) work when host git calls them.

Optional environment variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `GIT_FAT_IMAGE` | `git-fat:latest` | Docker image name |
| `GIT_FAT_VERBOSE` | unset | Verbose stderr output |
| `GIT_FAT_VERSION` | unset | Set to `1` for legacy v1 placeholders |

SSH keys from `~/.ssh` are mounted read-only for rsync-over-ssh.

## Test

All tests run inside Docker:

```bash
./test.sh
```

## Commands

Same as upstream git-fat:

```bash
git fat init
git fat status
git fat push
git fat pull
git fat checkout
git fat verify
git fat gc
git fat find THRESH_BYTES
```

See [README.md](README.md) for the full workflow and `.gitfat` configuration.
