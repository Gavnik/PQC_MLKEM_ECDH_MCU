# RP2350 only (m33 and hz3, every placement): put the Pico SDK's .time_critical.* functions into
# Zephyr's .ramfunc section, so the MPU's ramfunc region (read-only + executable) covers them.
# See rp2350_time_critical.ld for the full story. A no-op on every other SoC.
if(CONFIG_SOC_SERIES_RP2350 AND CONFIG_ARCH_HAS_RAMFUNC_SUPPORT)
  zephyr_linker_sources(RAMFUNC_SECTION ${CMAKE_CURRENT_LIST_DIR}/rp2350_time_critical.ld)
  message(STATUS "PQC: RP2350 .time_critical -> .ramfunc (MPU-covered RAM functions)")
endif()
