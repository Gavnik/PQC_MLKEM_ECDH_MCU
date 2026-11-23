#!/usr/bin/env python3
"""Where did the crypto end up?  Proof of the hybrid layout: crypto CODE and crypto CONSTANT
TABLES both in SRAM, in two separate blocks (code block R+X, tables block R-only under W^X).

Reads <build>/zephyr/zephyr.elf with `readelf -sW` (works for ARM, RISC-V and Xtensa ELFs on the
host) and classifies every crypto function AND constant table by ADDRESS:
  RP2350   flash 0x10000000-  SRAM 0x20000000-0x2007FFFF (+SRAM8/9)
  PSoC62   flash 0x10000000-  SRAM 0x08000000-0x080FFFFF
  ESP32-S3 flash 0x42000000- (code) / 0x3C000000- (rodata); IRAM 0x40370000-; DRAM 0x3FC88000-
Functions/labels count as CODE, data objects (const tables) as CONSTANTS. Writable variables
(.data/.bss) are skipped -- they are in SRAM in every build.
  HYBRID build: code 100% SRAM, constants 100% SRAM.   FLASH build: both 100% flash.
  TABLES-ONLY hybrid (m4): code 100% flash, constants 100% SRAM.
Also prints the bounds/sizes of the relocated CODE block and TABLES block (for the W^X tiling).

usage: python3 placement_check.py build-m33-hyb-on [more build dirs ...]
"""
import re, subprocess, sys

CATS = [
    ("ML-KEM",        r"^(wc_MlKemKey_|mlkem_|wc_mlkem_|MLKEM_)"),
    ("Thumb-2 ML-KEM",r"^L?_?mlkem_thumb2"),
    ("SHA-3/SHAKE",   r"(BlockSha3|^wc_Sha3|^wc_Shake|^Sha3|^sha3_|^L_sha3)"),
    ("NIST SP math",  r"^(sp_(256|384|521)_|sp_ecc_|p(256|384|521)_)"),
    ("ECC glue",      r"^(wc_ecc_|_ecc_|ecc_)"),
    ("X25519",        r"^(curve25519|wc_curve25519|fe_|ge_|base$|L_curve25519)"),
    ("RNG+SHA-256",   r"^(wc_RNG_|wc_InitRng|Hash_DRBG|_InitRng|Hash_df|Hash_gen|wc_Sha256|Transform_Sha256|L_SHA256|K$)"),
]
FLASH = [(0x10000000, 0x18000000), (0x42000000, 0x44000000), (0x3C000000, 0x3E000000)]
RAM   = [(0x20000000, 0x20100000), (0x08000000, 0x08100000), (0x40370000, 0x403E0000), (0x3FC80000, 0x3FD00000)]
where = lambda a: "SRAM" if any(lo <= a < hi for lo, hi in RAM) else "flash" if any(lo <= a < hi for lo, hi in FLASH) else "other"

def check(build):
    elf = f"{build}/zephyr/zephyr.elf"
    out = subprocess.run(["readelf", "-sW", elf], capture_output=True, text=True)
    if out.returncode:
        print(f"== {build}: cannot read {elf}"); return False
    sec = subprocess.run(["readelf", "-SW", elf], capture_output=True, text=True).stdout
    secname = {}
    for m in re.finditer(r"\[\s*(\d+)\]\s+(\S+)\s+(\S+)\s+[0-9a-f]+\s+[0-9a-f]+\s+[0-9a-f]+\s+\S+\s+(\S*)", sec):
        secname[m.group(1)] = (m.group(2), m.group(4))          # index -> (name, flags)
    allsyms = {t.split()[7]: int(t.split()[1], 16) for t in out.stdout.splitlines()
               if len(t.split()) >= 8 and t.split()[1] != "Value" and re.fullmatch(r"[0-9a-f]+", t.split()[1])}
    # the relocated tables block (Zephyr reloc on Cortex-M/RISC-V, our _pqc_tables_* on ESP32, where
    # it lives inside the writable .dram0.data output section and must not be skipped as a variable)
    tb = [(allsyms[a], allsyms[b]) for a, b in (("__ram_rodata_reloc_start", "__ram_rodata_reloc_end"),
                                                ("_pqc_tables_start", "_pqc_tables_end"))
          if a in allsyms and b in allsyms and allsyms[b] > allsyms[a]]
    in_tables = lambda a: any(lo <= a < hi for lo, hi in tb)
    syms = {}
    for line in out.stdout.splitlines():
        t = line.split()
        if len(t) < 8 or t[3] not in ("FUNC", "OBJECT", "NOTYPE") or t[6] in ("UND", "ABS"):
            continue
        sname, flags = secname.get(t[6], ("?", ""))
        if t[3] == "OBJECT" and "W" in flags and "reloc" not in sname and not in_tables(int(t[1], 16)):
            continue      # writable section (.data/.bss, ESP32 .dram0.data): variables are always in
                          # SRAM, so they prove nothing either way. A constant still in FLASH is what
                          # the check must catch, and flash sections are never writable.
        kind = "const" if t[3] == "OBJECT" else "code"
        name = t[7].split(".")[0] if t[3] != "NOTYPE" else t[7]
        syms.setdefault((name, kind), int(t[1], 16) & ~1)
    print(f"== {build}")
    for label, a, b in (("code block  ", "__ram_text_reloc_start", "__ram_text_reloc_end"),
                        ("tables block", "__ram_rodata_reloc_start", "__ram_rodata_reloc_end"),
                        ("whole RO region", "_ram_mpu_ro_region_start", "_ram_mpu_ro_region_end"),
                        ("code block   (ESP32 IRAM)", "_pqc_code_start", "_pqc_code_end"),
                        ("tables block (ESP32 DRAM)", "_pqc_tables_start", "_pqc_tables_end")):
        lo, hi = allsyms.get(a), allsyms.get(b)
        if lo is not None and hi is not None:
            print(f"   {label}: 0x{lo:08x}..0x{hi:08x} = {(hi-lo)/1024:.1f} KB")
    places = {"code": set(), "const": set()}
    print(f"   {'':15s} {'CODE':24s} CONSTANTS")
    for cat, pat in CATS:
        cols, odd = [], []
        for kind in ("code", "const"):
            hits = {n: a for (n, k), a in syms.items() if k == kind and re.search(pat, n)}
            w = {}
            for n, a in hits.items():
                w.setdefault(where(a), []).append(n)
            places[kind].update(x for x in w if x != "other")
            cols.append("  ".join(f"{x} {len(v)}" for x, v in sorted(w.items())) or "-")
            if len(w) > 1:
                for x, v in sorted(w.items()):
                    odd.append(f"      {kind} in {x}: " + ", ".join(sorted(v)[:8]) + (" ..." if len(v) > 8 else ""))
        print(f"   {cat:15s} {cols[0]:24s} {cols[1]}")
        for o in odd: print(o)
    c, k = places["code"], places["const"]
    verdict = ("HYBRID OK: crypto code in SRAM, no crypto constant left in flash" if c == {"SRAM"} and "flash" not in k else
               "FLASH build: code and constants in flash" if c == {"flash"} and "SRAM" not in k else
               "TABLES-ONLY HYBRID OK: crypto code in flash, every crypto constant in SRAM"
                   if c == {"flash"} and k == {"SRAM"} else
               f"UNEXPECTED: code in {sorted(c)}, constants in {sorted(k)} (see lines above)")
    print(f"   VERDICT: {verdict}")
    return True

for b in sys.argv[1:]:
    check(b.rstrip("/"))
