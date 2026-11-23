#!/usr/bin/env python3
"""ESP32-S3 HYBRID placement: crypto CODE into IRAM and crypto CONSTANTS into DRAM.

Zephyr's ESP32-S3 port has no CONFIG_CODE_DATA_RELOCATION, so hybrid is done by input-section
lines in soc/espressif/esp32s3/default.ld, guarded by CONFIG_PQC_CRYPTO_IN_IRAM
(config/overlay-iram-esp32.conf):
   code   (.literal/.text) -> .iram0.text  (internal SRAM, instruction bus; IRAM_ATTR-style)
   consts (.rodata)        -> .dram0.data  (internal SRAM, data bus; DRAM_ATTR-style --
                                            IRAM allows only 32-bit loads, tables need byte loads)
Same policy and the same object list as hyb_relocate.cmake (Cortex-M / RISC-V).

What this script does (idempotent, safe to rerun):
  1. backs up default.ld once  (default.ld.pqc-orig)
  2. REMOVES every existing block guarded by CONFIG_PQC_CRYPTO_IN_IRAM (your WP0 patch included),
     printing what it removed
  3. finds the wolfSSL archive name in an existing ESP32 build (ar member scan), and
  4. inserts the two fresh guarded blocks (code -> IRAM, constants -> DRAM).
Without CONFIG_PQC_CRYPTO_IN_IRAM=y the blocks are preprocessed away -> flash builds unchanged.
Each block is bracketed by _pqc_code_start/_end and _pqc_tables_start/_end; the tables block has
256-byte aligned ends, the PMS split-line granularity, so the firmware can make exactly that block
read-only with the DRAM0 split lines D_0/D_1 (three-way W^X, main.c esp32_pms_apply()).

usage: python3 esp32_iram_patch.py --build ~/zephyrproject/build-lx7 \
           [--ld ~/zephyrproject/zephyr/soc/espressif/esp32s3/default.ld]
"""
import argparse, os, re, shutil, sys

OBJS = [  # same list as hyb_relocate.cmake
    "wc_mlkem", "wc_mlkem_poly", "sha3", "thumb2-mlkem-asm_c", "thumb2-sha3-asm_c",
    "ecc", "sp_int", "sp_c32", "sp_cortexm", "wolfmath",
    "curve25519", "fe_operations", "ge_operations", "fe_low_mem", "ge_low_mem", "thumb2-curve25519_c",
    "random", "sha256", "hash", "thumb2-sha256-asm_c", "memory",
]
GUARD = "CONFIG_PQC_CRYPTO_IN_IRAM"

def ar_members(path):
    """Member names of a GNU/SysV ar archive (handles the // long-name table)."""
    names, longtab = [], b""
    with open(path, "rb") as f:
        if f.read(8) != b"!<arch>\n":
            return names
        while True:
            hdr = f.read(60)
            if len(hdr) < 60:
                break
            name, size = hdr[:16].rstrip(), int(hdr[48:58].strip() or 0)
            data = f.read(size)
            if size % 2:
                f.read(1)
            if name == b"//":
                longtab = data
            elif name in (b"/", b"/SYM64/"):
                continue
            elif name.startswith(b"/") and name[1:].isdigit():
                off = int(name[1:]); end = longtab.find(b"/\n", off)
                names.append(longtab[off:end].decode())
            else:
                names.append(name.decode().rstrip("/"))
    return names

def find_wolfssl_archive(build):
    hits = []
    for root, _, files in os.walk(build):
        for fn in files:
            if fn.endswith(".a"):
                p = os.path.join(root, fn)
                if "wc_mlkem.c.obj" in ar_members(p):
                    hits.append(fn)
    return sorted(set(hits))

def strip_old_blocks(lines):
    out, i, removed = [], 0, []
    while i < len(lines):
        if re.match(r"\s*#\s*if.*\b" + GUARD + r"\b", lines[i]):
            depth, j = 0, i
            while j < len(lines):
                if re.match(r"\s*#\s*if", lines[j]): depth += 1
                elif re.match(r"\s*#\s*endif", lines[j]):
                    depth -= 1
                    if depth == 0: break
                j += 1
            removed.append("".join(lines[i:j + 1]))
            # also drop our own marker comment directly above, if present
            if out and "PQC-HYBRID" in out[-1]:
                out.pop()
            i = j + 1
            continue
        out.append(lines[i]); i += 1
    return out, removed

def block(archive, kinds, comment, sym, align, indent="    "):
    """Guarded input-section block, bracketed by _pqc_<sym>_start/_end so the firmware (PMS split
    lines) and placement_check.py can find it. align = boundary for both ends (256 B for the DRAM
    tables block = the PMS split-line granularity; 4 B for the IRAM code block)."""
    b = [f"{indent}/* PQC-HYBRID {comment} (tools/esp32_iram_patch.py; list = hyb_relocate.cmake) */\n",
         f"#if defined({GUARD})\n",
         f"{indent}. = ALIGN({align});\n",
         f"{indent}_pqc_{sym}_start = ABSOLUTE(.);\n"]
    for o in OBJS:
        b.append(f"{indent}*{archive}:{o}.c.obj({kinds})\n")
    b += [f"{indent}. = ALIGN({align});\n",
          f"{indent}_pqc_{sym}_end = ABSOLUTE(.);\n",
          "#endif\n"]
    return b

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", required=True, help="an existing ESP32-S3 build dir (e.g. build-lx7)")
    ap.add_argument("--ld", default=os.path.expanduser("~/zephyrproject/zephyr/soc/espressif/esp32s3/default.ld"))
    a = ap.parse_args()

    arcs = find_wolfssl_archive(os.path.expanduser(a.build))
    if len(arcs) != 1:
        sys.exit(f"expected exactly one archive containing wc_mlkem.c.obj in {a.build}, found {arcs} "
                 "-- build the flash lx7 image first")
    archive = arcs[0]
    print(f"[patch] wolfSSL archive: {archive}")

    if not os.path.exists(a.ld + ".pqc-orig"):
        shutil.copyfile(a.ld, a.ld + ".pqc-orig")
        print(f"[patch] backup: {a.ld}.pqc-orig")
    lines = open(a.ld).readlines()
    lines, removed = strip_old_blocks(lines)
    for r in removed:
        print("[patch] REMOVED old block:\n" + "".join("    | " + l for l in r.splitlines(True)))

    def find(pat, start=0):
        for k in range(start, len(lines)):
            if re.search(pat, lines[k]): return k
        sys.exit(f"anchor not found: {pat}")
    it = find(r"^\s*\.iram0\.text\s*:")
    k_iram = find(r"\*\(\.iram1 \.iram1\.\*\)", it)
    dd = find(r"^\s*\.dram0\.data\s*:")
    k_data = find(r"^\s*\*\(\.data\)\s*$", dd)
    # insert the later anchor first so the earlier index stays valid
    lines[k_data:k_data] = block(archive, ".rodata .rodata.*", "constants -> DRAM", "tables", 256)
    lines[k_iram + 1:k_iram + 1] = block(archive, ".literal .literal.* .text .text.*", "code -> IRAM", "code", 4)
    open(a.ld, "w").writelines(lines)
    print(f"[patch] wrote {a.ld}: {len(OBJS)} objects -> code in IRAM + constants in DRAM, guarded by {GUARD}")

if __name__ == "__main__":
    main()
