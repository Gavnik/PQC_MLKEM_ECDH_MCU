/* RP2350: Pico SDK "run from RAM" functions -> Zephyr's .ramfunc section.
 *
 * The Pico SDK marks its flash-access routines __not_in_flash_func, which puts them in
 * .time_critical.* sections (flash_do_cmd, flash_range_erase/program, flash_flush_cache,
 * xip_cache_*, ...). They must run from SRAM because they switch flash XIP off while running.
 * Zephyr's RP2350 linker script never names these sections, so they landed as orphans in plain
 * .data (seen: flash_do_cmd @0x20000284) -- SRAM that Zephyr's MPU maps execute-never.
 * At boot the USB stack reads the board serial number via flash_get_unique_id -> flash_do_cmd,
 * so with CONFIG_ARM_MPU=y the M33 died with IACCVIOL before the console (the WP0 "M33 + MPU
 * crash", root-caused 2026-11-09 with the boot-fault reporter).
 * Placed here, they sit in .ramfunc, which Zephyr copies to SRAM at boot and covers with its
 * own read-only + executable MPU region (arm_core_mpu.c static_regions[], ARCH_HAS_RAMFUNC_SUPPORT,
 * selected by the RP2350 SoC when XIP). Included by cmake/rp2350_time_critical.cmake. */
*(.time_critical*)
