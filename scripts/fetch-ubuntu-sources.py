#!/usr/bin/env python3
# Copyright (c) 2026 deemo0666 (Pallas Agent). SPDX-License-Identifier: MIT
"""Download the Ubuntu source packages behind every binary package in the Pallas Linux guest.

Input: ubuntu/source-packages.txt ("name version" per line). Output: one folder per source package
under the given directory, holding the .dsc and every file it lists, fetched from Launchpad.
"""
import pathlib, re, subprocess, sys

root = pathlib.Path(__file__).resolve().parent.parent
out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else root / "dl/ubuntu")
LP = "https://launchpad.net/ubuntu/+archive/primary/+sourcefiles/{}/{}/{}"

def get(url, dest):
    if dest.exists() and dest.stat().st_size:
        return True
    part = dest.with_suffix(dest.suffix + ".part")
    ok = subprocess.run(["curl", "-fsSL", "--retry", "3", "-o", part, url]).returncode == 0
    if ok:
        part.replace(dest)
    return ok

failed = []
for line in (root / "ubuntu/source-packages.txt").read_text().split("\n"):
    if not line.strip():
        continue
    name, version = line.split()
    bare = version.split(":", 1)[-1]
    folder = out / f"{name}_{bare}"
    folder.mkdir(parents=True, exist_ok=True)
    dsc = folder / f"{name}_{bare}.dsc"
    if not get(LP.format(name, version, dsc.name), dsc):
        failed.append(line); continue
    text = dsc.read_text(errors="replace")
    files = re.search(r"^Files:\n((?: .+\n?)+)", text, re.M)
    for entry in files.group(1).strip().split("\n"):
        file = entry.split()[-1]
        if not get(LP.format(name, version, file), folder / file):
            failed.append(f"{line} {file}")
    print("ok", name, version, flush=True)
print("FAILED:", *failed, sep="\n" if failed else " ")
