/* user_settings.h -- wolfSSL/wolfCrypt configuration for the Zephyr PQC benchmark.
 *
 * WHY THIS FILE EXISTS: Zephyr's wolfSSL Kconfig does NOT expose ML-KEM. Without this
 * header the module builds with no ML-KEM at all, wc_MlKemKey_Init() fails (or fails to
 * link), and -- worse -- TLS silently negotiates a classical group. This is the exact
 * class of bug that produced fake "ML-KEM" numbers on the Raspberry Pi 4 study.
 *
 * IT IS WIRED IN BY prj.conf: CONFIG_WOLFSSL_SETTINGS_FILE="src/user_settings.h" makes
 * the wolfSSL Zephyr module compile with WOLFSSL_SETTINGS_FILE pointing here, and adds
 * the app's src/ to the include path. (Do NOT also define WOLFSSL_USER_SETTINGS -- the
 * two settings mechanisms conflict.) CONFIG_WOLFSSL_MLKEM=y additionally enables the
 * module's ML-KEM build hooks.
 *
 * PARITY WITH THE Pi 4 BUILD:
 *   - ML-KEM (FIPS-203) at all three levels
 *   - PURE / standalone ML-KEM as a TLS 1.3 group (NOT hybrid) -- must be explicit,
 *     wolfSSL defaults to hybrid-only
 *   - ECDSA P-256 for authentication (server cert), ECDHE + X25519 for the classical
 *     baseline groups
 *   - TLS 1.3 only
 */
#ifndef PQC_ZEPHYR_USER_SETTINGS_H
#define PQC_ZEPHYR_USER_SETTINGS_H

/* ---------------- platform ---------------- */
/* WOLFSSL_ZEPHYR is already defined on the compiler command line by the Zephyr module;
 * guard to avoid a redefinition warning. */
#ifndef WOLFSSL_ZEPHYR
#define WOLFSSL_ZEPHYR
#endif
#define WOLFSSL_GENERAL_ALIGNMENT 4
#define SINGLE_THREADED               /* benchmark runs in one thread */
#define WOLFSSL_SMALL_STACK           /* MCU: prefer heap over huge stack frames */
#define NO_FILESYSTEM                 /* certs are compiled in, not read from disk */
#define NO_WOLFSSL_DIR
#define NO_WRITEV
#define WOLFSSL_IGNORE_FILE_WARN
#define WOLFSSL_NO_CURRDIR

/* ---------------- socket layer selection (board-dependent) ----------------
 * When networking is DISABLED (the RISC-V Hazard3 Layer-1-only build), wolfSSL must NOT
 * reference the system htons()/ntohs()/sockets -- those aren't compiled in, and wolfio.h
 * would fail with "implicit declaration of htons". Defining WOLFSSL_NO_SOCK makes wolfSSL
 * use its own built-in byte-swap and skip the socket I/O layer. When networking IS enabled
 * (ARM Pico, ESP32-S3 for Layer 2), we leave sockets on so the TLS client works. This is
 * driven by Zephyr's CONFIG_NETWORKING so one settings file serves all boards. */
#include <zephyr/autoconf.h>
#if !defined(CONFIG_NETWORKING)
  #define WOLFSSL_NO_SOCK
  #define WOLFSSL_USER_IO
#endif

/* ---------------- TLS 1.3 only ---------------- */
#define WOLFSSL_TLS13
#define NO_OLD_TLS
#define HAVE_TLS_EXTENSIONS
#define HAVE_SUPPORTED_CURVES
#define HAVE_HKDF
#define WC_RSA_PSS                    /* required by the TLS1.3 code paths */
#define HAVE_SESSION_TICKET

/* ---------------- ML-KEM (FIPS-203) ---------------- */
#define WOLFSSL_HAVE_MLKEM            /* master switch */
#define WOLFSSL_WC_MLKEM              /* wolfCrypt's own ML-KEM (not the liboqs port) */
#define WOLFSSL_MLKEM_MAKEKEY
#define WOLFSSL_MLKEM_ENCAPSULATE
#define WOLFSSL_MLKEM_DECAPSULATE
/* all three security levels in ONE firmware, so a single image serves every run */
#define WOLFSSL_MLKEM_512
#define WOLFSSL_MLKEM_768
#define WOLFSSL_MLKEM_1024

/* CRITICAL: allow PURE (standalone) ML-KEM as a TLS 1.3 named group.
 * wolfSSL ships hybrid-first; without this, set_groups(mlkemXXX) is REJECTED and a
 * naive client silently falls back to a classical group. The Pi 4 study hit exactly
 * this. Keep the client's negotiated-group readback as the runtime proof. */
#define WOLFSSL_MLKEM_STANDALONE
#undef  WOLFSSL_TLS_NO_MLKEM_STANDALONE
/* (If your wolfSSL revision spells this differently, grep configure.ac for
 *  "mlkem-standalone" and mirror the macro it defines. VERIFY on hardware with the
 *  NEGOTIATED: line before trusting any Layer 2 number.) */

/* ---------------- SHA-3 / SHAKE (ML-KEM depends on these) ---------------- */
#define WOLFSSL_SHA3
#define WOLFSSL_SHAKE128
#define WOLFSSL_SHAKE256

/* ---------------- classical baseline groups + auth ---------------- */
#define HAVE_ECC                      /* P-256 / P-384 / P-521 */
#define HAVE_ECC384
#define HAVE_ECC521
#define ECC_SHAMIR
#define HAVE_CURVE25519               /* x25519 baseline group */
#define HAVE_ED25519                  /* harmless; some builds want it alongside */
#define HAVE_SUPPORTED_CURVES
/* server cert is ECDSA P-256 -> we must be able to VERIFY ECDSA */
#define HAVE_ECC_VERIFY
#define HAVE_ECC_SIGN
/* ECC side-channel hardening = wolfSSL's ./configure default (decided 2026-11-06, gap 2).
 * Before this it was undefined, which made settings.h warn "consider using harden options".
 * Our P-256/384/521 keygen+derive dispatch to the SP code (sp_cortexm.c / sp_c32.c), which is
 * constant-time by construction and does not test this macro; the macro hardens wolfSSL's
 * generic ecc.c point multiply (not on our path) and makes shared-secret require an RNG
 * (main.c ecc_setup already calls wc_ecc_set_rng under #ifdef ECC_TIMING_RESISTANT).
 * NO_RSA is set below and we use SP math (not fastmath), so this alone clears the warning;
 * TFM_TIMING_RESISTANT / WC_RSA_BLINDING are not applicable. */
#define ECC_TIMING_RESISTANT

/* ---------------- ECC acceleration: single-precision (SP) math ----------------
 * CRITICAL for a FAIR benchmark. Without SP math, wolfSSL's ECC falls back to the generic
 * heap bignum (integer.c) and the NIST curves are catastrophically slow (measured on the
 * M33: P-256 ~140 ms, P-384 ~330 ms, P-521 ~712 ms). SP math is wolfSSL's optimised ECC
 * path, and when WOLFSSL_ARMASM(_THUMB2) is also set (it is, via the board conf) the SP
 * routines use the Cortex-M assembly -> the "best available implementation per primitive".
 * NOTE (corrected 2026-10-30): wolfSSL ships Thumb-2 ML-KEM assembly (thumb2-mlkem-asm), and it
 * compiles+links on the Cortex-M boards, but our A/B shows NO speedup (asm-on == asm-off), and
 * there is none for RV32 or Xtensa. Gap 3 (2026-11-06) found WHY there was no speedup: the
 * WOLFSSL_ARMASM switches were never set (see the Thumb-2 block below) -- the asm was never
 * called. Now enabled on Cortex-M asm-on builds; hz3 and LX7 stay reference C.
 * The asm here is therefore about the CLASSICAL ECDH baseline only: without it the NIST curves
 * run unoptimised C and we'd be comparing C-ECDH against C-ML-KEM on an artificially slow ECDH.
 * Best-available-per-primitive (asm ECC vs reference-C ML-KEM) keeps each side optimal.
 * WOLFSSL_SP_MATH_ALL = SP-based math for ALL sizes (TLS/cert-parse safe). P-256 is covered
 * by default once SP ECC is on; P-384 and P-521 are opt-in. */
#define WOLFSSL_HAVE_SP_ECC
#define WOLFSSL_SP_MATH_ALL
#define WOLFSSL_SP_384
#define WOLFSSL_SP_521
/* SP ASSEMBLY for the NIST curves on Cortex-M. The wolfSSL Zephyr module compiles
 * wolfcrypt/src/sp_cortexm.c; this macro activates its Cortex-M3+ assembly (and compiles
 * out the slow C fallback in sp_c32.c for these ops). Without it, SP math runs in pure C
 * (measured: P-256 keygen ~34 ms); with it we expect low-single-digit ms -- the optimised ECDH
 * path, so the classical baseline is as fast as wolfSSL can make it on this core. (ML-KEM's own
 * Thumb-2 asm is enabled by the separate WOLFSSL_ARMASM block below.)
 * GUARDED on Cortex-M so the SAME settings file stays correct everywhere: the two ARM
 * boards (M33 + the Infineon M4s) get the assembly; RISC-V Hazard3 and Xtensa LX7 have no
 * SP-ECC assembly in wolfSSL and keep the portable C path -- the "best available per
 * platform" rule. */
/* Tie the SP-ECC assembly to the WOLFCRYPT_ARMASM switch (not just CONFIG_CPU_CORTEX_M), so the
 * RQ3 asm-OFF control (CONFIG_WOLFCRYPT_ARMASM=n) disables the SP-ECC Cortex-M asm AND the
 * Thumb-2 X25519/Ed25519 asm in a single flip -> a pure portable-C ECDH baseline (ML-KEM is
 * already C regardless) to compare M33-C against Hazard3-C.
 * Normal builds set WOLFCRYPT_ARMASM=y via the board conf, so the asm stays on by default. */
#if defined(CONFIG_CPU_CORTEX_M) && defined(CONFIG_WOLFCRYPT_ARMASM)
  #define WOLFSSL_SP_ARM_CORTEX_M_ASM
#endif

/* THUMB-2 ASSEMBLY for ML-KEM (NTT/basemul/rej_uniform), SHA-3/SHAKE, X25519, SHA-2, AES,
 * ChaCha/Poly1305 on Cortex-M. FIXED 2026-11-06 (gap 3): until now this block was MISSING.
 * The wolfSSL Zephyr module sets these in ITS OWN zephyr/user_settings.h (under
 * CONFIG_WOLFCRYPT_ARMASM), but CONFIG_WOLFSSL_SETTINGS_FILE replaces that file with this one,
 * so the module's CMakeLists still compiled port/arm/thumb2-*_c.c while their contents compiled
 * out (#ifdef WOLFSSL_ARMASM) and every caller took the C path. Proof: readelf of the asm-on
 * m4/m33 ELFs showed only the CONFIG_*THUMB2 Kconfig symbols, no thumb2 functions. => all
 * earlier "asm on" rows = NIST-curve SP asm ONLY.
 * The set below = the module's own (WOLFSSL_ARMASM, WOLFSSL_NO_HASH_RAW, WOLFSSL_ARMASM_INLINE,
 * WOLFSSL_ARMASM_NO_NEON) + WOLFSSL_ARMASM_THUMB2 (selects the thumb2_* functions; without it
 * wc_mlkem.h picks the arm32 ones, which this build does not compile) + WOLFSSL_ARMASM_NO_HW_CRYPTO
 * (M4/M33 have no AES/SHA instructions), matching wolfSSL's Cortex-M4/M7 reference config
 * (examples/configs/user_settings_stm32.h). Gated on the SAME Kconfig switches that make the
 * module compile the thumb2 files, so asm-off builds stay pure C. */
#if defined(CONFIG_CPU_CORTEX_M) && defined(CONFIG_WOLFCRYPT_ARMASM) && \
    defined(CONFIG_WOLFCRYPT_ARMASM_THUMB2)
  #define WOLFSSL_ARMASM
  #define WOLFSSL_NO_HASH_RAW
  #define WOLFSSL_ARMASM_INLINE
  #define WOLFSSL_ARMASM_NO_NEON
  #define WOLFSSL_ARMASM_THUMB2
  #define WOLFSSL_ARMASM_NO_HW_CRYPTO
#endif

/* ---------------- symmetric + hashes for TLS 1.3 ---------------- */
#define HAVE_AESGCM
#define WOLFSSL_AES_GCM
#define WOLFSSL_SHA256
#define WOLFSSL_SHA384
#define WOLFSSL_SHA512
#define HAVE_AEAD

/* ---------------- RNG ---------------- */
#define HAVE_HASHDRBG
/* Seed source for the Hash-DRBG: Zephyr's entropy driver (CONFIG_ENTROPY_GENERATOR=y) on the
 * RP2350 and ESP32-S3 (hardware TRNGs); on the PSoC62, CUSTOM_RAND_GENERATE_SEED routes to the
 * PDL hardware TRNG (psoc6_trng_seed() in main.c). Layer 1 does not mask interrupts around timed
 * ops, so an interrupt-driven entropy source would also work. The ML-KEM encaps/decaps keys
 * and ciphertexts of round r come from pre-shared seeds (main.c kem_seed_bytes), not the DRBG. */

/* ---------------- time source (FALLBACK, normally OFF) ----------------
 * wolfSSL's Zephyr port falls back to POSIX clock_gettime(), which needs
 * CONFIG_POSIX_API=y + CONFIG_POSIX_TIMERS=y in prj.conf (the primary fix).
 * If your Zephyr/picolibc still won't expose clock_gettime(), enable this block to
 * make wolfSSL use a custom time function backed by Zephyr uptime instead -- no POSIX
 * needed. The XTIME implementation lives in main.c (guarded by the same macro).
 * NOTE: uptime-based time is fine here because the benchmark uses VERIFY_NONE (no cert
 * date validation); it only needs a monotonically sane time_t. */
#ifdef PQC_USE_UPTIME_TIME
  #define WOLFSSL_USER_CURRTIME
  #define USER_TIME
  #define XTIME(tl) pqc_xtime((tl))
  #include <time.h>
  time_t pqc_xtime(time_t *timer);
#endif

/* ---------------- trim what we do not use (flash/RAM on MCU) ---------------- */
#define NO_DES3
#define NO_DSA
#define NO_RC4
#define NO_MD4
#define NO_MD5
#define NO_PSK
#define NO_PWDBASED
#define NO_DH
#define NO_RSA                        /* auth is ECDSA now; RSA not needed */

/* ---------------- PSoC62 (Infineon CAT1A) hardware-TRNG seed ----------------
 * This board has NO Zephyr entropy driver / DT node for its MXCRYPTO Crypto-block TRNG, so
 * Zephyr's sys_rand_get() is unbacked (link error z_impl_sys_rand_get). The silicon TRNG IS
 * present and the vendor HAL (cyhal_trng) drives it. We override wolfSSL's seed source to read
 * that HARDWARE TRNG directly, so (a) wolfSSL never references sys_rand_get, and (b) the DRBG is
 * seeded from real hardware entropy -- the SAME KIND of source as the RP2350/ESP32-S3 silicon
 * TRNGs; only the plumbing differs (HAL call vs Zephyr driver). The crypto implementation is
 * unchanged, and the seed is a tiny one-time draw per op, so cross-board timing stays comparable.
 * CONFIG_SOC_FAMILY_INFINEON_CAT1A is available via <zephyr/autoconf.h> included above.
 * Implementation: psoc6_trng_seed() in src/main.c. */
#if defined(CONFIG_SOC_FAMILY_INFINEON_CAT1A)
  int psoc6_trng_seed(unsigned char* output, unsigned int sz);
  #define CUSTOM_RAND_GENERATE_SEED(buf, sz)  psoc6_trng_seed((buf), (sz))
#endif

#endif /* PQC_ZEPHYR_USER_SETTINGS_H */
