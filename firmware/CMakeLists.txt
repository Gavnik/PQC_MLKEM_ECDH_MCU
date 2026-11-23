# CMakeLists.txt -- Zephyr application: ML-KEM vs ECDH benchmark.
# ONE app for all four CPU cores (RP2350 Cortex-M33 and Hazard3, PSoC 62 Cortex-M4F,
# ESP32-S3 Xtensa LX7), selected only by -b.
cmake_minimum_required(VERSION 3.20.0)
set(ZEPHYR_EXTRA_MODULES $ENV{HOME}/zephyrproject/modules/crypto/wolfssl)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
include(${CMAKE_CURRENT_SOURCE_DIR}/cmake/hyb_relocate.cmake)
project(pqc_bench_zephyr)

target_sources(app PRIVATE src/main.c)
target_include_directories(app PRIVATE src)

# --- wolfSSL configuration -------------------------------------------------------
# Zephyr's wolfSSL Kconfig does NOT expose ML-KEM, so we supply src/user_settings.h and
# tell wolfSSL to honour it. Without this the module compiles WITHOUT ML-KEM and the
# benchmark fails at runtime (or, worse, TLS quietly negotiates a classical group).
# NOTE: do NOT define WOLFSSL_USER_SETTINGS here. wolfSSL's Zephyr module uses its own
# mechanism: CONFIG_WOLFSSL_SETTINGS_FILE (set in prj.conf) makes it define
# WOLFSSL_SETTINGS_FILE="src/user_settings.h" and adds the app's src/ to the include
# path. Defining WOLFSSL_USER_SETTINGS too would conflict (two settings mechanisms).
zephyr_include_directories(${CMAKE_CURRENT_SOURCE_DIR}/src)

# --- PSoC62 (Infineon CAT1A) HW TRNG: compile the one PDL crypto-core source that
# cyhal_trng_generate() calls (Cy_Crypto_Core_Trng). hal_infineon doesn't pull it in (no crypto
# DT node), so we add just this file. Cy_Crypto_Core_Enable + hw layer are already compiled
# (only _Trng was undefined), so nothing else is added -> no duplicate symbols.
if(CONFIG_SOC_FAMILY_INFINEON_CAT1A)
  set(_pdl $ENV{HOME}/zephyrproject/modules/hal/infineon/mtb-pdl-cat1/drivers/source)
  target_sources(app PRIVATE
    ${_pdl}/cy_crypto_core_trng.c
    ${_pdl}/cy_crypto_core_hw.c
    ${_pdl}/cy_crypto_core_hw_v1.c)
  set_source_files_properties(
    ${_pdl}/cy_crypto_core_trng.c ${_pdl}/cy_crypto_core_hw.c
    ${_pdl}/cy_crypto_core_hw_v1.c
    PROPERTIES COMPILE_FLAGS "-Wno-error")
endif()
include(${CMAKE_CURRENT_SOURCE_DIR}/cmake/rp2350_time_critical.cmake)
include(${CMAKE_CURRENT_SOURCE_DIR}/cmake/opt_split.cmake OPTIONAL)
