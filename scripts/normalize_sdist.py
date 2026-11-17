"""Normalize a source tarball's metadata for byte-reproducible releases.

Setuptools stamps generated members (``PKG-INFO``, directories) with wall-clock
time even when ``SOURCE_DATE_EPOCH`` is set, so two builds of the same commit can
produce different sdists. This script rewrites every member with a fixed mtime,
uid/gid 0 and an empty gzip header timestamp/name. It is release tooling, not
runtime code.
"""

from __future__ import annotations

import argparse
import gzip
import io
import sys
import tarfile
from pathlib import Path


def normalize(path: Path, epoch: int) -> None:
    """Rewrite ``path`` (a ``.tar.gz``) in place with deterministic metadata."""
    with tarfile.open(path, "r:gz") as source:
        members: list[tuple[tarfile.TarInfo, bytes | None]] = []
        for member in source.getmembers():
            data = source.extractfile(member).read() if member.isfile() else None
            member.mtime = epoch
            member.uid = 0
            member.gid = 0
            member.uname = ""
            member.gname = ""
            # setuptools writes PAX headers; tarfile prefers pax "mtime" over
            # TarInfo.mtime, so drop them or the wall-clock stamp survives.
            member.pax_headers = {}
            members.append((member, data))

    buffer = io.BytesIO()
    with (
        gzip.GzipFile(fileobj=buffer, mode="wb", mtime=epoch, filename="") as gz,
        tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as target,
    ):
        for member, data in members:
            target.addfile(member, io.BytesIO(data) if data is not None else None)
    path.write_bytes(buffer.getvalue())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--epoch", type=int, required=True)
    args = parser.parse_args(argv)
    if not args.path.is_file():
        print("not a file: %s" % args.path, file=sys.stderr)
        return 2
    normalize(args.path, args.epoch)
    print("normalized: %s (epoch %d)" % (args.path, args.epoch))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
