# opt_split.cmake -- optimisation split, identical on every board (2026-11-10):
#   wolfSSL (every source of the module library) + src/main.c (timing harness)  -> -O2
#   everything else (Zephyr kernel, drivers, libc)                               -> Kconfig level
#                                                    (prj.conf: CONFIG_SIZE_OPTIMIZATIONS = -Os)
# Why: see prj.conf. Source-file COMPILE_OPTIONS come after the target/interface options on the
# compiler command line, so this -O2 is the LAST -O flag and wins; verify in compile_commands.json.
# PQC_OPT_SPLIT_O2 is defined for the same files so main.c can self-report opt=split-O2.
# Default ON; -DPQC_OPT_SPLIT=0 only for diagnostics.
if(NOT DEFINED PQC_OPT_SPLIT)
  set(PQC_OPT_SPLIT 1)
endif()
if(PQC_OPT_SPLIT)
  set(_wt ..__modules__crypto__wolfssl__zephyr)
  if(NOT TARGET ${_wt})
    message(FATAL_ERROR "PQC opt split: wolfSSL target ${_wt} not found")
  endif()
  get_target_property(_wsrc ${_wt} SOURCES)
  set_source_files_properties(${_wsrc} TARGET_DIRECTORY ${_wt} PROPERTIES
    COMPILE_OPTIONS "-O2" COMPILE_DEFINITIONS "PQC_OPT_SPLIT_O2=1")
  set_source_files_properties(${CMAKE_CURRENT_SOURCE_DIR}/src/main.c PROPERTIES
    COMPILE_OPTIONS "-O2" COMPILE_DEFINITIONS "PQC_OPT_SPLIT_O2=1")
  list(LENGTH _wsrc _n)
  message(STATUS "PQC opt split: ${_n} wolfSSL sources + main.c at -O2")
endif()
