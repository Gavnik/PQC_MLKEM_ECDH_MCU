/* pqc_reloc_anchor.c -- only built for the tables-only hybrid (PQC_HYB_PART=tables, m4).
 *
 * Zephyr's Arm MPU code (arch/arm/core/mpu/arm_core_mpu.c) and main.c's fault tests refer to the
 * relocated-code symbols __ram_text_reloc_start / _size, which the linker only defines when some
 * code is relocated. A tables-only image relocates no wolfSSL code, so it failed to link
 * (2026-11-11). This one tiny function is relocated instead: it gives the image a minimal
 * (32-byte MPU region) read-only + executable code block, which the W^X tests then attack like
 * any other code block. It is never called by the benchmark. */
#include <stdint.h>

__attribute__((used, noinline)) uint32_t pqc_reloc_anchor(uint32_t x)
{
	return x + 1u;
}
