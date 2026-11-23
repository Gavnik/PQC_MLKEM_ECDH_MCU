# hyb_relocate.cmake -- HYBRID placement for Cortex-M (m33, m4) and RISC-V (hz3).  (2026-11-10)
#
# HYBRID = the crypto working set runs from SRAM, everything else from flash (XIP):
#   crypto CODE (.text)        flash -> SRAM at boot   (Zephyr startup copy, before main())
#   crypto CONSTANTS (.rodata) flash -> SRAM at boot   (ECC/SP base-point tables, Ed25519 base table,
#                                                       ML-KEM zetas, Keccak / SHA-256 constants)
# Three-way W^X, live from boot (m33/m4: Zephyr's MPU + tools/zephyr_tables_ro_patch.py;
# hz3: main.c locked PMP map, CONFIG_PQC_WX; ESP32: main.c PMS, see esp32_iram_patch.py):
#   flash                    R + X     (kernel, drivers, libc, harness)
#   SRAM crypto code block   R + X     (__ram_text_reloc_start .. _end)     never writable after boot
#   SRAM crypto tables block R only    (__ram_rodata_reloc_start .. _end)   not executable
#   rest of SRAM             R + W     not executable
# Nothing is ever writable and executable at once. Same idea as Espressif IRAM_ATTR/DRAM_ATTR and
# the ST/NXP/TI "RAM functions" practice, applied to the whole crypto working set.
# Active only when CONFIG_CODE_DATA_RELOCATION=y (overlay-hyb-arm.conf / overlay-hyb-rv.conf).
#
# ONE SUPERSET LIST FOR EVERY BOARD (parity). Zephyr's gen_relocate_app.py silently skips any
# listed file this build did not compile, so the same list is correct everywhere and each build
# relocates exactly the crypto it runs. The ESP32-S3 has no CODE_DATA_RELOCATION;
# tools/esp32_iram_patch.py writes the SAME list into its linker script (code -> IRAM, tables -> DRAM).
#
# LOCATION RAM_TEXT_RODATA: code then constants into the default RAM region; the generated linker
# script brackets them with separate start/end symbols (verified in gen_relocate_app.py), which is
# what makes the separate code-RX / tables-R protection possible. NOKEEP: unused functions in these
# files can still be dropped by --gc-sections (smaller SRAM footprint).

if(CONFIG_CODE_DATA_RELOCATION)
  if(DEFINED ZEPHYR_WOLFSSL_MODULE_DIR)
    set(_ws ${ZEPHYR_WOLFSSL_MODULE_DIR})
  else()
    set(_ws $ENV{HOME}/zephyrproject/modules/crypto/wolfssl)
  endif()
  set(_wc ${_ws}/wolfcrypt/src)

  set(PQC_RELOC_FILES
    # ML-KEM + its hashing
    ${_wc}/wc_mlkem.c
    ${_wc}/wc_mlkem_poly.c
    ${_wc}/sha3.c
    ${_wc}/port/arm/thumb2-mlkem-asm_c.c
    ${_wc}/port/arm/thumb2-sha3-asm_c.c
    # NIST curves (SP math): asm-on -> sp_cortexm.c, asm-off/RISC-V/Xtensa -> sp_c32.c
    ${_wc}/ecc.c
    ${_wc}/sp_int.c
    ${_wc}/sp_c32.c
    ${_wc}/sp_cortexm.c
    ${_wc}/wolfmath.c
    # X25519 (fast C, Thumb-2 asm, Ed25519 base table used by asm keygen, byte-wise SMALL)
    ${_wc}/curve25519.c
    ${_wc}/fe_operations.c
    ${_wc}/ge_operations.c
    ${_wc}/fe_low_mem.c
    ${_wc}/ge_low_mem.c
    ${_wc}/port/arm/thumb2-curve25519_c.c
    # RNG draw inside every keygen (Hash-DRBG over SHA-256)
    ${_wc}/random.c
    ${_wc}/sha256.c
    ${_wc}/hash.c
    ${_wc}/port/arm/thumb2-sha256-asm_c.c
    ${_wc}/memory.c
  )
  # PQC_HYB_PART (diagnostic, 2026-11-11; default "full" = every measured hybrid image):
  #   full   : code + tables -> SRAM   (RAM_TEXT_RODATA)  placement=hybrid
  #   code   : code only     -> SRAM   (RAM_TEXT)         placement=hybrid-code    tables stay in flash
  #   tables : tables only   -> SRAM   (RAM_RODATA)       placement=hybrid-tables  code stays in flash
  # m4 only (2026-11-11): its code runs no faster from SRAM (the M4 stalls when an instruction fetch
  # and a data access share a path; the flash cache keeps code fetches off the SRAM path), so the m4
  # also runs a "tables" image -- still three-way W^X: flash code R+X, SRAM tables R, data RW no-X.
  # "code" was the diagnostic that located the m4 slowdown in the code half (2026-11-11).
  if(NOT DEFINED PQC_HYB_PART)
    set(PQC_HYB_PART full)
  endif()
  if(PQC_HYB_PART STREQUAL "full")
    set(_loc RAM_TEXT_RODATA)
  elseif(PQC_HYB_PART STREQUAL "code")
    set(_loc RAM_TEXT)
    zephyr_compile_definitions(PQC_HYB_PART_CODE=1)
  elseif(PQC_HYB_PART STREQUAL "tables")
    set(_loc RAM_RODATA)
    zephyr_compile_definitions(PQC_HYB_PART_TABLES=1)
    # Zephyr's Arm MPU code needs a relocated code block to exist (__ram_text_reloc_*): relocate
    # one tiny never-called anchor function (src/pqc_reloc_anchor.c) so the block is 32 bytes.
    target_sources(app PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/src/pqc_reloc_anchor.c)
    zephyr_code_relocate(FILES ${CMAKE_CURRENT_SOURCE_DIR}/src/pqc_reloc_anchor.c LOCATION RAM_TEXT)
  else()
    message(FATAL_ERROR "PQC_HYB_PART must be full, code or tables (got '${PQC_HYB_PART}')")
  endif()
  zephyr_code_relocate(FILES ${PQC_RELOC_FILES} LOCATION ${_loc} NOKEEP)
  message(STATUS "PQC hybrid (${PQC_HYB_PART}): wolfCrypt ${_loc} -> SRAM (${_wc})")
endif()
