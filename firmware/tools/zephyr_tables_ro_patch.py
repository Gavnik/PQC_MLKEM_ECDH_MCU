#!/usr/bin/env python3
"""Three-way W^X on Cortex-M (m33, m4): make the hybrid's crypto TABLES block read-only + no-exec
with Zephyr's OWN MPU driver (CONFIG_ARM_MPU=y, CONFIG_CODE_DATA_RELOCATION_SRAM=y).

Zephyr already gives the relocated crypto CODE a read-only+executable region (arm_core_mpu.c,
static_regions[], __ram_text_reloc_*). It does nothing for the relocated .rodata, which therefore sits
in ordinary read/write SRAM. Two small, inert-for-others patches to the Zephyr tree:

 1. scripts/build/gen_relocate_app.py
    The relocated .rodata of the default RAM region gets the same MPU-aligned linker template the
    relocated .text already uses (MPU_ALIGN). Armv8-M MPU (m33): 32-byte aligned end. Armv7-M MPU
    (m4): end padded to a power of two, so the block is one legal PMSAv7 region.
 2. arch/arm/core/mpu/arm_core_mpu.c
    One more entry in static_regions[], right after the relocated-text entry:
       [__ram_rodata_reloc_start, +__ram_rodata_reloc_size)  K_MEM_PARTITION_P_RO_U_RO
    (= read-only for all, execute-never -- NOT_EXEC on both v7 and v8). The symbols are WEAK, so a
    build without relocated .rodata gets size 0, which Zephyr's MPU code skips.

 3. scripts/build/gen_relocate_app.py, MPU-aligned template (marker PQC-MPU-ALIGN)
    Zephyr pads an MPU-aligned relocated block with MPU_ALIGN(__X_reloc_size), but that size is
    defined AFTER the padding (end - start), so it refers to itself: any power of two above the
    real content is a valid solution, and ld picked 256 KB for 83.5 KB of m4 crypto code (~170 KB
    of SRAM wasted). The fix records the content end first and pads from the CONTENT size:
    MPU_ALIGN(__X_reloc_content_end - __X_reloc_start) -> the smallest legal power of two.
    No effect on the m33 (Armv8-M MPU: 32-byte alignment only) or on RISC-V.

 4. scripts/build/gen_relocate_app.py, section classifier (marker PQC-SRODATA)    [RISC-V only]
    The RISC-V compiler puts small constants (<= 8 bytes: the ecc_oid_* curve OIDs, a curve25519
    constant) in .srodata.* instead of .rodata.*. Zephyr classifies by the substring ".rodata.",
    which ".srodata." does not contain, so those constants were silently left in flash on hz3
    (placement_check: UNEXPECTED). The patch maps ".srodata." to RODATA -> they join the tables
    block. Arm and Xtensa never emit .srodata, so no effect there.
 5. scripts/build/gen_relocate_app.py, MPU-aligned template (marker PQC-PMP-TILE)  [RISC-V only]
    Under `#if defined(CONFIG_RISCV) && !defined(CONFIG_RISCV_PMP)` both relocated blocks are padded
    to a 32 KB boundary. Our hz3 PMP code (main.c; the RP2350 port has no Zephyr PMP) must tile each block into aligned power-of-two NAPOT entries; with 4-byte ends the code block
    alone needed 9 of the 8 entries, with 32 KB ends each block needs 2. Costs < 32 KB SRAM per block
    (measured 2.2 + 1.1 KB on the 2026-11-10 hybrid). Preprocessed away on Arm.

 6. scripts/build/gen_relocate_app.py, MPU-aligned template (marker PQC-MPU-START)  [PMSAv7 only]
    A PMSAv7 region must START on a multiple of its (power-of-two) size. Zephyr's template pads the
    END of each relocated block (MPU_ALIGN) but never aligns its START; the m4 blocks were valid only
    because the M4 RAM began at 0x08000000. With the RAM moved to 0x08002000 (clear of the M0+
    image, 2026-11-11) the code block started at 0x08002000, Zephyr silently dropped the code and
    tables MPU regions, and the hybrid's code/tables were writable (caught by the fault tests). The
    patch puts an empty NOLOAD pad section IN FRONT of each block that aligns the RAM location to
    the block's own size (inside the section would break Zephyr's boot copy, which reads from the
    section's load address), under CONFIG_MPU_REQUIRES_POWER_OF_TWO_ALIGNMENT
    (checked with GNU ld: 0x08002000 -> 0x08020000, load addresses consistent).
    No effect on the m33 (Armv8-M), hz3 or ESP32.

Idempotent (markers PQC-TABLES-RO / PQC-MPU-ALIGN / PQC-SRODATA / PQC-PMP-TILE / PQC-MPU-START), keeps
<file>.pqc-orig backups (of the ORIGINAL file, taken before the first patch), --check only reports.
usage: python3 zephyr_tables_ro_patch.py [--zephyr ~/zephyrproject/zephyr] [--check]
"""
import argparse, os, re, shutil, sys

MARK = "PQC-TABLES-RO"

def patch_gen_relocate(path, check):
    r1 = patch_gen_relocate_rodata(path, check)
    r2 = patch_gen_relocate_align(path, check)
    r3 = patch_gen_relocate_srodata(path, check)
    r4 = patch_gen_relocate_pmp_tile(path, check)
    r5 = patch_gen_relocate_start(path, check)
    return f"tables={r1}; content-size alignment={r2}; srodata={r3}; riscv 32K tiles={r4}; v7 start alignment={r5}"

START_MARK = "PQC-MPU-START"

def patch_gen_relocate_start(path, check):
    """Insert, IN FRONT of the MPU-aligned relocated section, an empty NOLOAD output section that
    pads the RAM location counter to the block's own power-of-two size. Padding inside the section
    would move __X_reloc_start away from the section start, while Zephyr's boot copy reads from
    LOADADDR(section) -> the copy would be shifted. A separate pad section keeps start == section
    start == copy source (verified with GNU ld 2.42, 2026-11-11: 0x08002000 -> 0x08020000)."""
    s = open(path).read()
    if START_MARK in s:
        return "already patched"
    if ALIGN_MARK not in s:
        return "SKIPPED (apply the PQC-MPU-ALIGN patch first: run again)"
    a1 = "                __{mem}_{kind}_reloc_start = .;\n                {linker_sections}\n"
    k = s.find(a1)
    if k < 0 or s.count(a1) != 1:
        sys.exit(f"{path}: LINKER_SECTION_SEQ_MPU start anchor not found -- Zephyr version differs")
    # the SECTION_PROLOGUE line that opens this section, and the closing '}} <region>' line after it
    p = s.rfind("SECTION_PROLOGUE(.{mem}_{kind}_reloc", 0, k)
    m = re.compile(r"\n(\s*)\}\}([^\n]*)\n").search(s, k)
    if p < 0 or not m or s.find('"""', p, k) >= 0:
        sys.exit(f"{path}: could not delimit the MPU-aligned section template -- Zephyr version differs")
    line0 = s.rfind("\n", 0, p) + 1
    indent, region = s[line0:p], m.group(2).strip()
    if check:
        return f"NOT patched (section found, region '{region}')"
    pad = (f"#if defined(CONFIG_MPU_REQUIRES_POWER_OF_TWO_ALIGNMENT)\n"
           f"{indent}/* {START_MARK}: PMSAv7 region must START on a multiple of its size */\n"
           f"{indent}.pqc_pad_{{mem}}_{{kind}} (NOLOAD) :\n"
           f"{indent}{{{{\n"
           f"{indent}        . = ALIGN(1 << LOG2CEIL(__{{mem}}_{{kind}}_reloc_content_end - __{{mem}}_{{kind}}_reloc_start));\n"
           f"{indent}}}}} {region}\n"
           f"#endif\n")
    s = s[:line0] + pad + s[line0:]
    return write(path, s)

SRODATA_MARK = "PQC-SRODATA"

def patch_gen_relocate_srodata(path, check):
    s = open(path).read()
    if SRODATA_MARK in s:
        return "already patched"
    a = '        elif ".rodata." in name:\n            return cls.RODATA\n'
    if s.count(a) != 1:
        sys.exit(f"{path}: SectionKind.for_section_named '.rodata.' branch not found -- Zephyr version differs")
    if check:
        return "NOT patched (anchor found)"
    s = s.replace(a, a + f'        elif ".srodata." in name:  # {SRODATA_MARK}: RISC-V small constants\n'
                         '            return cls.RODATA\n')
    return write(path, s)

TILE_MARK = "PQC-PMP-TILE"

def patch_gen_relocate_pmp_tile(path, check):
    s = open(path).read()
    if TILE_MARK in s:
        return "already patched"
    lines = s.split("\n")
    hits = [i for i, l in enumerate(lines) if ALIGN_MARK in l and "MPU_ALIGN(" in l]
    if len(hits) != 1:
        return "SKIPPED (apply the PQC-MPU-ALIGN patch first: run again)"
    if check:
        return "NOT patched (anchor found)"
    i = hits[0]
    lines[i + 1:i + 1] = [
        "#if defined(CONFIG_RISCV) && !defined(CONFIG_RISCV_PMP)",
        f"                . = ALIGN(0x8000);  /* {TILE_MARK}: 32 KB ends -> few NAPOT tiles for our hz3 PMP */",
        "#endif",
    ]
    return write(path, "\n".join(lines))

ALIGN_MARK = "PQC-MPU-ALIGN"

def patch_gen_relocate_align(path, check):
    s = open(path).read()
    if ALIGN_MARK in s:
        return "already patched"
    a1 = "                __{mem}_{kind}_reloc_start = .;\n                {linker_sections}\n"
    a2 = "                MPU_ALIGN(__{mem}_{kind}_reloc_size);\n"
    if s.count(a1) != 1 or s.count(a2) != 1:
        sys.exit(f"{path}: LINKER_SECTION_SEQ_MPU template anchors not found -- Zephyr version differs")
    if check:
        return "NOT patched (anchors found)"
    s = s.replace(a1, a1 + "                __{mem}_{kind}_reloc_content_end = .;\n")
    s = s.replace(a2, "                MPU_ALIGN(__{mem}_{kind}_reloc_content_end - __{mem}_{kind}_reloc_start);"
                      f"  /* {ALIGN_MARK}: pad from CONTENT size (non-circular) */\n")
    return write(path, s)

def patch_gen_relocate_rodata(path, check):
    s = open(path).read()
    if MARK in s:
        return "already patched"
    pat = re.compile(r"(if region_is_default_ram\(memory_type\) and kind in \(\s*\n"
                     r"(\s*)SectionKind\.TEXT,\s*\n\s*SectionKind\.LITERAL,\s*\n)")
    m = pat.search(s)
    if not m:
        sys.exit(f"{path}: anchor (default-RAM TEXT/LITERAL -> LINKER_SECTION_SEQ_MPU) not found -- "
                 "Zephyr version differs; send me that part of the file")
    if check:
        return "NOT patched (anchor found)"
    ind = m.group(2)
    new = m.group(1) + f"{ind}SectionKind.RODATA,  # {MARK}: MPU-align the relocated tables block too\n"
    return write(path, s[:m.start()] + new + s[m.end():])

def patch_mpu(path, check):
    s = open(path).read()
    if MARK in s:
        return "already patched"
    a = "extern char __ram_text_reloc_size[];\n"
    i = s.find(a)
    j = s.find("RAM area for relocated text")
    k = s.find("#endif /* CONFIG_CODE_DATA_RELOCATION_SRAM */", j) if j >= 0 else -1
    if i < 0 or j < 0 or k < 0:
        sys.exit(f"{path}: anchors (relocated-text extern / static region) not found -- Zephyr version differs")
    if check:
        return "NOT patched (anchors found)"
    ext = (f"/* {MARK}: relocated crypto constant tables (weak: size 0 = skipped if absent) */\n"
           "extern char __ram_rodata_reloc_start[] __attribute__((weak));\n"
           "extern char __ram_rodata_reloc_size[] __attribute__((weak));\n")
    region = (f"\t{{\n"
              f"\t\t/* {MARK}: relocated crypto constant tables -> read-only, execute-never */\n"
              f"\t\t.start = (uint32_t)&__ram_rodata_reloc_start,\n"
              f"\t\t.size = (uint32_t)&__ram_rodata_reloc_size,\n"
              f"\t\t.attr = K_MEM_PARTITION_P_RO_U_RO,\n"
              f"\t}},\n")
    # insert the region just BEFORE the #endif that closes the relocated-text entry (still guarded
    # by CONFIG_CODE_DATA_RELOCATION_SRAM), then the externs after the text externs
    s = s[:k] + region + s[k:]
    i = s.find(a)
    s = s[:i + len(a)] + ext + s[i + len(a):]
    return write(path, s)

def write(path, s):
    if not os.path.exists(path + ".pqc-orig"):
        shutil.copyfile(path, path + ".pqc-orig")
    open(path, "w").write(s)
    return "PATCHED (backup: .pqc-orig)"

ap = argparse.ArgumentParser()
ap.add_argument("--zephyr", default=os.path.expanduser("~/zephyrproject/zephyr"))
ap.add_argument("--check", action="store_true")
a = ap.parse_args()
for rel, fn in (("scripts/build/gen_relocate_app.py", patch_gen_relocate),
                ("arch/arm/core/mpu/arm_core_mpu.c", patch_mpu)):
    p = os.path.join(a.zephyr, rel)
    print(f"[{MARK}] {rel}: {fn(p, a.check)}")
