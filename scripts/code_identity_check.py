#!/usr/bin/env python3
"""code_identity_check.py -- is the crypto code identical in two builds? (2026-11-12)

Usage:  python3 code_identity_check.py BUILD_A BUILD_B [BUILD_A2 BUILD_B2 ...]

For the W^X-off control: each pair is a measured v7 build and its W^X-off twin. For every wolfSSL
object file, the disassembly (with relocations, without addresses of the file
header) is compared. Identical disassembly means the crypto code is the same instructions, so any
time difference between the two images comes from the memory protection, not from the code.
main.c is left out on purpose: it contains the protection setup itself, which differs by design.
The objdump comes from each build's CMakeCache (CMAKE_OBJDUMP), so Arm, RISC-V and Xtensa work.
"""
import hashlib
import os
import subprocess
import sys


def objdump_of(build):
    for line in open(os.path.join(build, "CMakeCache.txt"), errors="replace"):
        if line.startswith("CMAKE_OBJDUMP:"):
            return line.split("=", 1)[1].strip()
    sys.exit(f"{build}: no CMAKE_OBJDUMP in CMakeCache.txt")


def objects(build):
    out = {}
    for root, _, files in os.walk(build):
        for f in files:
            if not f.endswith(".obj"):
                continue
            p = os.path.join(root, f)
            rel = os.path.relpath(p, build)
            if "wolfssl" in rel.lower():
                out[rel] = p
    return out


def code_hash(objdump, path):
    txt = subprocess.run([objdump, "-d", "-r", path], capture_output=True, text=True).stdout
    # objdump's "# <symbol+offset>" annotation (RISC-V) names the nearest symbol, often a debug-string
    # label (.LASFn) whose offset moves with the build-directory name; it is a comment, not code.
    lines = [l.split(" # ", 1)[0].rstrip() for l in txt.splitlines()]
    body = "\n".join(l for l in lines if "file format" not in l and path not in l)
    return hashlib.sha256(body.encode()).hexdigest()


def main():
    args = sys.argv[1:]
    if len(args) < 2 or len(args) % 2:
        sys.exit(__doc__)
    bad_pairs = 0
    for a, b in zip(args[0::2], args[1::2]):
        oa, ob = objects(a), objects(b)
        da, db = objdump_of(a), objdump_of(b)
        common = sorted(set(oa) & set(ob))
        only = sorted(set(oa) ^ set(ob))
        diff = [r for r in common if code_hash(da, oa[r]) != code_hash(db, ob[r])]
        verdict = "IDENTICAL" if common and not diff and not only else "DIFFERENT"
        if verdict != "IDENTICAL":
            bad_pairs += 1
        print(f"{a} vs {b}: {len(common)} objects compared, {len(diff)} differ, {len(only)} in one build only -> {verdict}")
        for r in diff[:10]:
            print(f"    differs: {r}")
        for r in only[:10]:
            print(f"    only in one build: {r}")
    print("ALL IDENTICAL" if bad_pairs == 0 else f"{bad_pairs} pair(s) DIFFERENT -- send this output")


if __name__ == "__main__":
    main()
