"""Core git-fat implementation."""

from __future__ import annotations

import collections
import errno
import hashlib
import itertools
import os
import subprocess
import sys
import tempfile
import threading
import time
from typing import BinaryIO, Callable, Iterator, TextIO

BLOCK_SIZE = 4096
GIT_FAT_COOKIE = b"#$# git-fat "


def verbose_stderr(*args, **kwargs) -> None:
    print(*args, file=sys.stderr, **kwargs)


def verbose_ignore(*args, **kwargs) -> None:
    pass


def mkdir_p(path: str) -> None:
    try:
        os.makedirs(path)
    except OSError as exc:
        if exc.errno == errno.EEXIST and os.path.isdir(path):
            pass
        else:
            raise


def umask() -> int:
    """Get umask without changing it."""
    old = os.umask(0)
    os.umask(old)
    return old


def readblocks(stream: BinaryIO) -> Iterator[bytes]:
    while True:
        data = stream.read(BLOCK_SIZE)
        if not data:
            break
        yield data


def cat_iter(initer: Iterator[bytes], outstream: BinaryIO) -> None:
    for block in initer:
        outstream.write(block)


def cat(instream: BinaryIO, outstream: BinaryIO) -> None:
    cat_iter(readblocks(instream), outstream)


def difftreez_reader(input: BinaryIO) -> Iterator[tuple[str, str, str]]:
    """Incremental reader for git diff-tree -z output."""
    buffer: list[str] = []
    partial = b""
    while True:
        newread = input.read(BLOCK_SIZE)
        if not newread:
            break
        partial += newread
        while True:
            head, sep, partial = partial.partition(b"\0")
            if not sep:
                partial = head
                break
            buffer.append(head.decode("utf-8", "surrogateescape"))
            if len(buffer) == 2:
                oldmode, newmode, oldhash, newhash, modflag = buffer[0].split()
                path = buffer[1]
                yield (newhash, modflag, path)
                buffer = []


def gitconfig_get(name: str, file: str | None = None) -> str | None:
    args = ["git", "config", "--get"]
    if file is not None:
        args += ["--file", file]
    args.append(name)
    p = subprocess.Popen(args, stdout=subprocess.PIPE, text=True)
    output, _ = p.communicate()
    output = output.strip()
    if p.returncode and file is None:
        return None
    if p.returncode:
        return gitconfig_get(name)
    return output or None


def gitconfig_set(name: str, value: str, file: str | None = None) -> None:
    args = ["git", "config"]
    if file is not None:
        args += ["--file", file]
    args += [name, value]
    subprocess.check_call(args)


class GitFat:
    DecodeError = RuntimeError

    def __init__(self) -> None:
        self.verbose: Callable[..., None] = (
            verbose_stderr if os.environ.get("GIT_FAT_VERBOSE") else verbose_ignore
        )
        try:
            self.gitroot = subprocess.check_output(
                ["git", "rev-parse", "--show-toplevel"], text=True
            ).strip()
        except subprocess.CalledProcessError:
            sys.exit(1)
        self.gitdir = subprocess.check_output(
            ["git", "rev-parse", "--git-dir"], text=True
        ).strip()
        self.objdir = os.path.join(self.gitdir, "fat", "objects")
        if os.environ.get("GIT_FAT_VERSION") == "1":
            self.encode = self.encode_v1
        else:
            self.encode = self.encode_v2

        def magiclen(enc: Callable[[str, int], str]) -> int:
            return len(enc(hashlib.sha1(b"dummy").hexdigest(), 5))

        self.magiclen = magiclen(self.encode)
        self.magiclens = [magiclen(enc) for enc in [self.encode_v1, self.encode_v2]]

    def setup(self) -> None:
        mkdir_p(self.objdir)

    def is_init_done(self) -> bool:
        return bool(
            gitconfig_get("filter.fat.clean") or gitconfig_get("filter.fat.smudge")
        )

    def assert_init_done(self) -> None:
        if not self.is_init_done():
            sys.stderr.write(
                "fatal: git-fat is not yet configured in this repository.\n"
            )
            sys.stderr.write('Run "git fat init" to configure.\n')
            sys.exit(1)

    def get_rsync(self) -> tuple[str, str | None, str | None, str | None]:
        cfgpath = os.path.join(self.gitroot, ".gitfat")
        remote = gitconfig_get("rsync.remote", file=cfgpath)
        ssh_port = gitconfig_get("rsync.sshport", file=cfgpath)
        ssh_user = gitconfig_get("rsync.sshuser", file=cfgpath)
        options = gitconfig_get("rsync.options", file=cfgpath)
        if remote is None:
            raise RuntimeError(f"No rsync.remote in {cfgpath}")
        return remote, ssh_port, ssh_user, options

    def get_rsync_command(self, push: bool) -> list[str]:
        remote, ssh_port, ssh_user, options = self.get_rsync()
        if push:
            self.verbose(f"Pushing to {remote}")
        else:
            self.verbose(f"Pulling from {remote}")

        cmd = [
            "rsync",
            "--progress",
            "--ignore-existing",
            "--from0",
            "--files-from=-",
        ]
        rshopts = ""
        if ssh_user:
            rshopts += " -l " + ssh_user
        if ssh_port:
            rshopts += " -p " + ssh_port
        if rshopts:
            cmd.append("--rsh=ssh" + rshopts)
        if options:
            cmd += options.split()
        if push:
            cmd += [self.objdir + "/", remote + "/"]
        else:
            cmd += [remote + "/", self.objdir + "/"]
        return cmd

    def revparse(self, revname: str) -> str:
        return subprocess.check_output(
            ["git", "rev-parse", revname], text=True
        ).strip()

    def encode_v1(self, digest: str, nbytes: int) -> str:
        return f"#$# git-fat {digest}\n"

    def encode_v2(self, digest: str, nbytes: int) -> str:
        return f"#$# git-fat {digest} {nbytes:20d}\n"

    def decode(self, data: str | bytes, noraise: bool = False) -> tuple[str | None, int | None]:
        if isinstance(data, bytes):
            text = data.decode("ascii", errors="replace")
        else:
            text = data
        cookie = "#$# git-fat "
        if text.startswith(cookie):
            parts = text[len(cookie) :].split()
            digest = parts[0]
            nbytes = int(parts[1]) if len(parts) > 1 else None
            return digest, nbytes
        if noraise:
            return None, None
        raise GitFat.DecodeError(f"Could not decode {data!r}")

    def decode_stream(
        self, stream: BinaryIO
    ) -> tuple[str | Iterator[bytes] | None, int | None]:
        preamble = stream.read(self.magiclen)
        try:
            return self.decode(preamble)
        except GitFat.DecodeError:
            return itertools.chain([preamble], readblocks(stream)), None

    def decode_file(self, fname: str) -> tuple[str | None, int | None]:
        try:
            stat = os.lstat(fname)
        except OSError:
            return None, None
        if stat.st_size != self.magiclen:
            return None, None
        try:
            with open(fname, "rb") as fh:
                digest, nbytes = self.decode_stream(fh)
        except OSError:
            return None, None
        if isinstance(digest, str):
            return digest, nbytes
        return None, nbytes

    def decode_clean(self, body: bytes) -> str | None:
        digest, _ = self.decode(body, noraise=True)
        return digest

    def filter_clean(self, instream: BinaryIO, outstream: BinaryIO) -> None:
        h = hashlib.sha1()
        nbytes = 0
        fd, tmpname = tempfile.mkstemp(dir=self.objdir)
        try:
            ishanging = False
            cached = False
            with os.fdopen(fd, "wb") as cache:
                write_stream: BinaryIO = cache
                firstblock = True
                for block in readblocks(instream):
                    if firstblock:
                        if len(block) == self.magiclen and self.decode_clean(
                            block[: self.magiclen]
                        ):
                            ishanging = True
                            write_stream = outstream
                        firstblock = False
                    h.update(block)
                    nbytes += len(block)
                    write_stream.write(block)
                write_stream.flush()
            digest = h.hexdigest()
            objfile = os.path.join(self.objdir, digest)
            if not ishanging:
                if os.path.exists(objfile):
                    self.verbose(
                        f"git-fat filter-clean: cache already exists {objfile}"
                    )
                    os.remove(tmpname)
                else:
                    os.chmod(tmpname, 0o444 & ~umask())
                    os.rename(tmpname, objfile)
                    self.verbose(f"git-fat filter-clean: caching to {objfile}")
                cached = True
                outstream.write(self.encode(digest, nbytes).encode("ascii"))
        finally:
            if not cached:
                os.remove(tmpname)

    def cmd_filter_clean(self) -> None:
        self.setup()
        self.filter_clean(sys.stdin.buffer, sys.stdout.buffer)

    def cmd_filter_smudge(self) -> None:
        self.setup()
        result, nbytes = self.decode_stream(sys.stdin.buffer)
        if isinstance(result, str):
            objfile = os.path.join(self.objdir, result)
            try:
                with open(objfile, "rb") as fh:
                    cat(fh, sys.stdout.buffer)
                self.verbose(f"git-fat filter-smudge: restoring from {objfile}")
            except OSError:
                self.verbose(f"git-fat filter-smudge: fat object missing {objfile}")
                sys.stdout.buffer.write(self.encode(result, nbytes).encode("ascii"))
        else:
            self.verbose("git-fat filter-smudge: not a managed file")
            cat_iter(result, sys.stdout.buffer)

    def catalog_objects(self) -> set[str]:
        return {
            name
            for name in os.listdir(self.objdir)
            if len(name) == 40 and all(c in "0123456789abcdef" for c in name)
        }

    def referenced_objects(self, rev: str | None = None, all: bool = False) -> set[str]:
        referenced: set[str] = set()
        if all:
            rev = "--all"
        elif rev is None:
            rev = self.revparse("HEAD")

        p1 = subprocess.Popen(
            ["git", "rev-list", "--objects", rev], stdout=subprocess.PIPE
        )

        def cut_sha1hash(input: BinaryIO, output: BinaryIO) -> None:
            for line in input:
                output.write(line.split()[0] + b"\n")
            output.close()

        p2 = subprocess.Popen(
            ["git", "cat-file", "--batch-check"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )

        def filter_gitfat_candidates(input: BinaryIO, output: BinaryIO) -> None:
            for line in input:
                objhash, objtype, size = line.split()
                if objtype == b"blob" and int(size) in self.magiclens:
                    output.write(objhash + b"\n")
            output.close()

        p3 = subprocess.Popen(
            ["git", "cat-file", "--batch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )

        cut_thread = threading.Thread(target=cut_sha1hash, args=(p1.stdout, p2.stdin))
        filter_thread = threading.Thread(
            target=filter_gitfat_candidates, args=(p2.stdout, p3.stdin)
        )
        cut_thread.start()
        filter_thread.start()

        while True:
            metadata_line = p3.stdout.readline()
            if not metadata_line:
                break
            objhash, objtype, size_str = metadata_line.split()
            size = int(size_str)
            bytes_read = 0
            content = b""
            while bytes_read < size:
                data = p3.stdout.read(size - bytes_read)
                if not data:
                    break
                content += data
                bytes_read += len(data)
            try:
                fathash = self.decode(content)[0]
                if fathash:
                    referenced.add(fathash)
            except GitFat.DecodeError:
                pass
            bytes_read = 0
            while bytes_read < 1:
                data = p3.stdout.read(1)
                if not data:
                    break
                bytes_read += len(data)

        cut_thread.join()
        filter_thread.join()
        p1.wait()
        p2.wait()
        p3.wait()
        return referenced

    def orphan_files(self, patterns: list[str] | None = None) -> Iterator[tuple[str, str]]:
        if not patterns or patterns == [""]:
            patterns = ["."]
        output = subprocess.check_output(["git", "ls-files", "-z"] + patterns)
        for fname in output.split(b"\0")[:-1]:
            path = fname.decode("utf-8", "surrogateescape")
            digest = self.decode_file(path)[0]
            if digest:
                yield (digest, path)

    def cmd_status(self, args: list[str]) -> None:
        self.setup()
        refargs: dict = {}
        if "--all" in args:
            refargs["all"] = True
        referenced = self.referenced_objects(**refargs)
        catalog = self.catalog_objects()
        garbage = catalog - referenced
        orphans = referenced - catalog
        if "--all" in args:
            for obj in referenced:
                print(obj)
        if orphans:
            print("Orphan objects:")
            for orph in orphans:
                print(f"    {orph}")
        if garbage:
            print("Garbage objects:")
            for g in garbage:
                print(f"    {g}")

    def cmd_push(self, args: list[str]) -> None:
        self.setup()
        pushall = "--all" in args
        files = self.referenced_objects(all=pushall) & self.catalog_objects()
        cmd = self.get_rsync_command(push=True)
        self.verbose(f"Executing: {' '.join(cmd)}")
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        p.communicate(input=b"\0".join(f.encode("ascii") for f in files))
        if p.returncode:
            sys.exit(p.returncode)

    def checkout(self, show_orphans: bool = False) -> None:
        self.assert_init_done()
        for digest, fname in self.orphan_files():
            objpath = os.path.join(self.objdir, digest)
            if os.access(objpath, os.R_OK):
                print(f"Restoring {digest} -> {fname}")
                stat = os.lstat(fname)
                os.utime(fname, (stat.st_atime, stat.st_mtime + 1))
                subprocess.check_call(
                    ["git", "checkout-index", "--index", "--force", fname]
                )
            elif show_orphans:
                print(f"Data unavailable: {digest} {fname}")

    def cmd_pull(self, args: list[str]) -> None:
        self.setup()
        refargs: dict = {}
        if "--all" in args:
            refargs["all"] = True
        for arg in args:
            if arg.startswith("-") or len(arg) != 40:
                continue
            rev = self.revparse(arg)
            if rev:
                refargs["rev"] = rev
        files = self.filter_objects(refargs, self.parse_pull_patterns(args))
        cmd = self.get_rsync_command(push=False)
        self.verbose(f"Executing: {' '.join(cmd)}")
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        p.communicate(input=b"\0".join(f.encode("ascii") for f in files))
        if p.returncode:
            sys.exit(p.returncode)
        self.checkout()

    def parse_pull_patterns(self, args: list[str]) -> list[str]:
        if "--" not in args:
            return [""]
        idx = args.index("--")
        return args[idx + 1 :]

    def filter_objects(self, refargs: dict, patterns: list[str]) -> set[str]:
        files = self.referenced_objects(**refargs) - self.catalog_objects()
        if refargs.get("all"):
            return files
        orphans_objects = {digest for digest, _ in self.orphan_files(patterns)}
        return files & orphans_objects

    def cmd_checkout(self, args: list[str]) -> None:
        self.checkout(show_orphans=True)

    def cmd_gc(self) -> None:
        garbage = self.catalog_objects() - self.referenced_objects()
        print(f"Unreferenced objects to remove: {len(garbage)}")
        for obj in garbage:
            fname = os.path.join(self.objdir, obj)
            print(f"{os.stat(fname).st_size:10d} {obj}")
            os.remove(fname)

    def cmd_verify(self) -> None:
        corrupted_objects = []
        for obj in self.catalog_objects():
            fname = os.path.join(self.objdir, obj)
            h = hashlib.sha1()
            with open(fname, "rb") as fh:
                for block in readblocks(fh):
                    h.update(block)
            data_hash = h.hexdigest()
            if obj != data_hash:
                corrupted_objects.append((obj, data_hash))
        if corrupted_objects:
            print(f"Corrupted objects: {len(corrupted_objects)}")
            for obj, data_hash in corrupted_objects:
                print(f"{obj} data hash is {data_hash}")
            sys.exit(1)

    def cmd_init(self) -> None:
        self.setup()
        if self.is_init_done():
            print("Git fat already configured, check configuration in .git/config")
        else:
            gitconfig_set("filter.fat.clean", "git-fat filter-clean")
            gitconfig_set("filter.fat.smudge", "git-fat filter-smudge")
            print("Initialized git fat")

    def gen_large_blobs(
        self, revs: str, threshsize: int
    ) -> Iterator[tuple[str, int]]:
        time0 = time.time()

        def hash_only(input: BinaryIO, output: BinaryIO) -> None:
            for line in input:
                output.write(line[:40] + b"\n")
            output.close()

        revlist = subprocess.Popen(
            ["git", "rev-list", "--all", "--objects"],
            stdout=subprocess.PIPE,
            bufsize=-1,
        )
        objcheck = subprocess.Popen(
            ["git", "cat-file", "--batch-check"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            bufsize=-1,
        )
        hashonly = threading.Thread(target=hash_only, args=(revlist.stdout, objcheck.stdin))
        hashonly.start()
        numblobs = 0
        numlarge = 0
        for line in objcheck.stdout:
            objhash, blob, size = line.split()
            if blob != b"blob":
                continue
            size_int = int(size)
            numblobs += 1
            if size_int > threshsize:
                numlarge += 1
                yield objhash.decode("ascii"), size_int
        revlist.wait()
        objcheck.wait()
        hashonly.join()
        time1 = time.time()
        self.verbose(
            f"{numlarge} of {numblobs} blobs are >= {threshsize} bytes "
            f"[elapsed {time1 - time0:.3f}s]"
        )

    def cmd_find(self, args: list[str]) -> None:
        maxsize = int(args[0])
        blobsizes = dict(self.gen_large_blobs("--all", maxsize))
        time0 = time.time()
        pathsizes: collections.defaultdict[str, set[int]] = collections.defaultdict(set)
        revlist = subprocess.Popen(
            ["git", "rev-list", "--all"], stdout=subprocess.PIPE, bufsize=-1
        )
        difftree = subprocess.Popen(
            [
                "git",
                "diff-tree",
                "--root",
                "--no-renames",
                "--no-commit-id",
                "--diff-filter=AMCR",
                "-r",
                "--stdin",
                "-z",
            ],
            stdin=revlist.stdout,
            stdout=subprocess.PIPE,
        )
        for newblob, modflag, path in difftreez_reader(difftree.stdout):
            bsize = blobsizes.get(newblob)
            if bsize:
                pathsizes[path].add(bsize)
        time1 = time.time()
        self.verbose(f"Found {len(pathsizes)} paths in {time1 - time0:.3f} s")
        maxlen = max(map(len, pathsizes), default=0)
        for path, sizes in sorted(
            pathsizes.items(), key=lambda ps: max(ps[1]), reverse=True
        ):
            print(
                f"{path:<{maxlen}s} filter=fat -text # {max(sizes):10d} {len(sizes)}"
            )
        revlist.wait()
        difftree.wait()

    def cmd_index_filter(self, args: list[str]) -> None:
        manage_gitattributes = "--manage-gitattributes" in args
        filelist = {f.strip() for f in open(args[0]).readlines()}
        lsfiles = subprocess.Popen(["git", "ls-files", "-s"], stdout=subprocess.PIPE)
        updateindex = subprocess.Popen(
            ["git", "update-index", "--index-info"], stdin=subprocess.PIPE
        )

        def dofilter(catfile_stdout: BinaryIO, hashobject_stdin: BinaryIO) -> None:
            self.filter_clean(catfile_stdout, hashobject_stdin)
            hashobject_stdin.close()

        for line in lsfiles.stdout:
            mode, sep, tail = line.decode("utf-8", "surrogateescape").partition(" ")
            blobhash, sep, tail = tail.partition(" ")
            stageno, sep, tail = tail.partition("\t")
            filename = tail.strip()
            if filename not in filelist:
                continue
            if mode == "120000":
                continue
            hashfile = os.path.join(self.gitdir, "fat", "index-filter", blobhash)
            try:
                with open(hashfile) as fh:
                    cleanedobj = fh.read().rstrip()
            except OSError:
                catfile = subprocess.Popen(
                    ["git", "cat-file", "blob", blobhash], stdout=subprocess.PIPE
                )
                hashobject = subprocess.Popen(
                    ["git", "hash-object", "-w", "--stdin"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                )
                filterclean = threading.Thread(
                    target=dofilter, args=(catfile.stdout, hashobject.stdin)
                )
                filterclean.start()
                cleanedobj = hashobject.stdout.read().decode("ascii").rstrip()
                catfile.wait()
                hashobject.wait()
                filterclean.join()
                mkdir_p(os.path.dirname(hashfile))
                with open(hashfile, "w") as fh:
                    fh.write(cleanedobj + "\n")
            updateindex.stdin.write(
                f"{mode} {cleanedobj} {stageno}\t{filename}\n".encode(
                    "utf-8", "surrogateescape"
                )
            )

        if manage_gitattributes:
            try:
                parts = subprocess.check_output(
                    ["git", "ls-files", "-s", ".gitattributes"], text=True
                ).split()
                mode, blobsha1, stageno, filename = parts[0], parts[1], parts[2], parts[3]
                gitattributes_lines = subprocess.check_output(
                    ["git", "cat-file", "blob", blobsha1], text=True
                ).splitlines()
            except ValueError:
                mode, stageno = "100644", "0"
                gitattributes_lines = []
            gitattributes_extra = [
                f"{line.split()[0]} filter=fat -text" for line in filelist
            ]
            hashobject = subprocess.Popen(
                ["git", "hash-object", "-w", "--stdin"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
            )
            stdout, _ = hashobject.communicate(
                ("\n".join(gitattributes_lines + gitattributes_extra) + "\n").encode(
                    "utf-8"
                )
            )
            updateindex.stdin.write(
                f"{mode} {stdout.strip().decode('ascii')} {stageno}\t.gitattributes\n".encode(
                    "utf-8"
                )
            )

        updateindex.stdin.close()
        lsfiles.wait()
        updateindex.wait()
