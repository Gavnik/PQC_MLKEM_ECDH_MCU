#include <zephyr/kernel.h>   /* k_sleep() to let lower-prio system threads run */
/* mcu_measure_core.h -- shared Layer 1 / Layer 2 measurement core for MCUs
 * (Raspberry Pi Pico 2 W  RP2350  ARM Cortex-M33 or RISC-V Hazard3,
 *  Espressif ESP32-S3     Xtensa LX7).
 *
 * DESIGN GOAL: emit the SAME "ROUND ..." and "AGG ..." lines as the Raspberry
 * Pi 4 harness (measure_core.h / l2_core.h) so the SAME Python parser
 * (pqcbench.py) ingests MCU results with ZERO changes. Only the transport
 * differs: the Pi prints to stdout over SSH; the MCU prints to UART/USB-CDC.
 *
 * The board-specific layer must provide:
 *    uint64_t mcu_now_us(void);     // monotonic microseconds
 *    void     mcu_print(const char*);// write a line to UART/USB-CDC
 *    (RNG + wolfCrypt are provided by the wolfSSL port for each board.)
 *
 * Philosophy parity with the Pi harness:
 *   - Layer 1: iters mode = fixed iteration count, warmup, N rounds; report
 *     per-round mean/sd/min/med/p95/max in MICROSECONDS (us_*), plus an AGG line
 *     with across-round mean/sd. (Same as Pi measure_core.h.)
 *   - Layer 2: iters mode = fixed handshake count, warmup, N rounds; report
 *     per-round ms_* and an AGG line. (Same as Pi l2_core.h.)
 *   - time/energy mode: run for a wall-clock window, emit PAIR_WITH_METER so the
 *     external SUNSHINE/PPK2 meter reading lines up with a known op count.
 */
#ifndef MCU_MEASURE_CORE_H
#define MCU_MEASURE_CORE_H
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <math.h>

/* ---- board hooks (defined in the board main) ---- */
uint64_t mcu_now_us(void);
void     mcu_print(const char *s);

/* ---- small print helper: build a line then emit it ---- */
static char _mcu_line[256];
#define MCU_EMIT(...) do{ snprintf(_mcu_line,sizeof _mcu_line,__VA_ARGS__); mcu_print(_mcu_line);}while(0)

/* ---- sorting + percentiles over uint64 samples (times in ns) ---- */
static int _mcu_cmp_u64(const void*a,const void*b){
    uint64_t x=*(const uint64_t*)a,y=*(const uint64_t*)b; return (x>y)-(x<y);
}

/* ---- cooperative yield (outside every timed region) ----
 * The benchmark runs in main() at a priority >= the system threads, so it must periodically
 * BLOCK (k_sleep) so lower-priority threads (USB-CDC, the watchdog feeder) get CPU time; on the
 * RP2350 a long enough no-yield window wedges/resets the board (documented in main(): ~25-28 s).
 * The original rule -- sleep 1 ms every 64 ops -- assumed ms-scale ops. For SLOW ops it leaves a
 * huge no-yield window: Hazard3 p521 derive is ~380 ms/op, so 10 warmup + 64 timed ops (+2 setup
 * keygens) = ~28.7 s without a yield -> board went silent; the identical chip on M33 at 335 ms/op
 * (~25.3 s) survived. So we ALSO yield once MCU_YIELD_MAX_MS of wall-clock has passed since the
 * last yield. For fast ops the 64-op rule always fires first, so their behavior is unchanged;
 * only ops slower than ~MCU_YIELD_MAX_MS/64 gain an extra (1 ms, untimed) yield.
 * The yield happens BETWEEN ops; each op records its own elapsed time internally, so no
 * measurement includes it. */
#ifndef MCU_YIELD_MAX_MS
#define MCU_YIELD_MAX_MS 10000
#endif
static int64_t _mcu_last_yield_ms;
static inline void mcu_yield_reset(void) { _mcu_last_yield_ms = k_uptime_get(); }
static inline void _mcu_do_yield(void)   { k_sleep(K_MSEC(1)); _mcu_last_yield_ms = k_uptime_get(); }
/* op-count OR wall-clock rule (Layer-1/2 loops) */
static inline void mcu_maybe_yield(long i)
{
    if (((i & 0x3F) == 0x3F) || (k_uptime_get() - _mcu_last_yield_ms >= MCU_YIELD_MAX_MS))
        _mcu_do_yield();
}
/* wall-clock-only rule (energy window: keeps idle time ~0.01% so energy/op is undisturbed) */
static inline void mcu_maybe_yield_time(void)
{
    if (k_uptime_get() - _mcu_last_yield_ms >= MCU_YIELD_MAX_MS) _mcu_do_yield();
}

/* A single "operation" returns 0 on success and writes elapsed nanoseconds to *ns.
 * For Layer 1 this is one keygen/encaps/decaps; for Layer 2 one full handshake. */
typedef int (*mcu_op_fn)(void *ctx, uint64_t *ns);

/* Optional per-round reset hook: called BEFORE each round (outside the timed region).
 * Used to Free+re-Init the crypto context between rounds so that any per-call internal
 * allocation in the crypto library cannot accumulate across a long multi-round run
 * (observed: ML-KEM keygen leaked/exhausted the heap around ~5000 total ops -> board
 * hung). Returns 0 on success. Pass NULL if no reset is needed. */
typedef int (*mcu_reset_fn)(void *ctx);

/* Shared runner. unit_is_ms=0 -> Layer 1 (report us_*), unit_is_ms=1 -> Layer 2 (ms_*).
 * label = the group/phase string that appears in the output line. */
static void mcu_run(const char *label, int unit_is_ms,
                    long count, long rounds, long warmup,
                    mcu_op_fn op, void *ctx,
                    uint64_t *scratch /* size >= count */,
                    mcu_reset_fn reset /* may be NULL */)
{
    /* warmup (not timed, not printed) */
    mcu_yield_reset();
    for (long i=0;i<warmup;i++){ uint64_t t; op(ctx,&t); mcu_maybe_yield(i); }

    double *central = (double*)0; /* avoid malloc; track across-round on the fly */
    double ar_mean=0, ar_M2=0; long ar_n=0;

    for (long r=0;r<rounds;r++){
        /* reset the crypto context between rounds to stop cross-round heap accumulation */
        if (reset && reset(ctx) != 0) {
            MCU_EMIT("ROUND %ld group=%s ok=0 RESET_FAILED", r, label);
            continue;
        }
        long n=0, discarded=0, failed=0;
        for (long i=0;i<count;i++){
            uint64_t t;
            int rc = op(ctx,&t);
            if (rc == 0)        scratch[n++]=t;   /* good sample */
            else if (rc == -2)  discarded++;      /* op >= 25 s: dual-clock discard */
            else                failed++;         /* crypto/socket failure */
            /* Briefly SLEEP every 64 ops so LOWER-priority system threads get CPU time.
             * ROOT CAUSE of a long-standing hang: the benchmark runs in main() at a
             * priority >= system threads. k_yield() only yields to equal/higher-priority
             * threads, so the lower-priority WATCHDOG-FEEDER thread was starved during a
             * long tight compute loop -> the hardware watchdog was never fed -> the board
             * reset/wedged at its ~28 s timeout (512x5 @18s survived, 768x5 @28s did not).
             * k_sleep() actually blocks main so the feeder (and USB) run. It is OUTSIDE the
             * timed region (each op already recorded its elapsed time), so measurements are
             * unaffected -- it only adds a little wall-clock time to the run.
             * (Now also time-capped -- see mcu_maybe_yield -- so slow ops can't starve.) */
            mcu_maybe_yield(i);
        }
        k_sleep(K_MSEC(2));                        /* round boundary: let system threads run */
        mcu_yield_reset();
        if (n==0){
            MCU_EMIT("ROUND %ld group=%s mode=iters ok=0 ALL_OPS_FAILED discarded=%ld failed=%ld",
                     r, label, discarded, failed);
            continue;
        }
        if (discarded || failed){
            MCU_EMIT("WARN round=%ld group=%s kept=%ld of %ld discarded=%ld(>=25s) failed=%ld",
                     r, label, n, count, discarded, failed);
        }
        /* Welford mean/sd in ns */
        double mean=0,M2=0;
        for (long i=0;i<n;i++){ double d=(double)scratch[i]-mean; mean+=d/(i+1); M2+=d*((double)scratch[i]-mean); }
        double sd = n>1 ? sqrt(M2/(n-1)) : 0.0;
        qsort(scratch,n,sizeof(uint64_t),_mcu_cmp_u64);
        double mn=scratch[0], mx=scratch[n-1];
        double med=scratch[n/2], p95=scratch[(long)(n*0.95)];

        double div = unit_is_ms ? 1e6 : 1e3;   /* ns->ms or ns->us */
        /* Across-round central value = the per-round MEAN (our PRIMARY metric and the one
         * we compare on), NOT the median. Averaging medians was inconsistent with the
         * methodology and noisier (the median of a discrete-quantized sample jumps between
         * adjacent values round-to-round, which inflated the across-round SD on the longest
         * op, e.g. decaps-1024). */
        double central_val = mean/div;
        /* accumulate across-round (Welford on the per-round MEAN) */
        { double d=central_val-ar_mean; ar_n++; ar_mean+=d/ar_n; ar_M2+=d*(central_val-ar_mean); }

        if (unit_is_ms){
            MCU_EMIT("ROUND %ld group=%s mode=iters handshakes=%ld "
                     "ms_mean=%.4f ms_sd=%.4f ms_min=%.4f ms_med=%.4f ms_p95=%.4f ms_max=%.4f",
                     r,label,n, mean/div,sd/div,mn/div,med/div,p95/div,mx/div);
        } else {
            MCU_EMIT("ROUND %ld group=%s mode=iters iters=%ld "
                     "us_mean=%.4f us_sd=%.4f us_min=%.4f us_med=%.4f us_p95=%.4f us_max=%.4f",
                     r,label,n, mean/div,sd/div,mn/div,med/div,p95/div,mx/div);
        }
    }
    double ar_sd = ar_n>1 ? sqrt(ar_M2/(ar_n-1)) : 0.0;
    if (unit_is_ms)
        MCU_EMIT("AGG group=%s rounds=%ld across_round_mean_ms=%.4f across_round_sd_ms=%.4f",
                 label,rounds,ar_mean,ar_sd);
    else
        MCU_EMIT("AGG group=%s rounds=%ld across_round_mean_us=%.4f across_round_sd_us=%.4f",
                 label,rounds,ar_mean,ar_sd);
    (void)central;
}

/* ---- RAW SAMPLE DUMP (per-round streaming to the laptop) ----
 * Emits every sample of ONE round as integer NANOSECONDS so the laptop can compute the
 * TRUE mean/sd/min/max/median/p95/p99 over ALL rounds*count samples (percentiles cannot
 * be recovered from per-round summaries). Framing:
 *     RAWSTART round=<r> group=<label> n=<N> unit=ns
 *     SAMPLES <v> <v> ... (up to 16 per line, integer ns)
 *     ...
 *     RAWEND round=<r> group=<label> n=<N>
 * The laptop's integrity checks rely on this being byte-exact: min/max/count MUST match
 * the ROUND summary (strict), so we print raw integers (no rounding, no unit conversion).
 * This runs OUTSIDE the timed region (all samples already recorded), so it does not affect
 * measurements -- it only adds wall-clock transfer time between rounds. */
static void mcu_dump_raw(const char *label, long r, const uint64_t *scratch, long n)
{
    MCU_EMIT("RAWSTART round=%ld group=%s n=%ld unit=ns", r, label, n);
    /* build lines of up to 16 integers each */
    char line[256]; int pos; long i = 0;
    while (i < n) {
        pos = 0;
        pos += snprintf(line+pos, sizeof(line)-pos, "SAMPLES");
        for (int k = 0; k < 16 && i < n; k++, i++) {
            pos += snprintf(line+pos, sizeof(line)-pos, " %llu",
                            (unsigned long long)scratch[i]);
            if (pos >= (int)sizeof(line) - 24) { i++; break; } /* safety; shouldn't trigger */
        }
        mcu_print(line);
        /* PACE THE DUMP. A raw round is sent as many SAMPLES lines back-to-back; with no
         * gap the USB-CDC console TX ring fills faster than the USB ISR drains it and the
         * board wedges mid-dump (observed: silent board at 1000/round; occasional lost
         * round even at 200/round). A 1 ms sleep per line yields the CPU so the TX ISR
         * drains a full USB frame before the next line -> the ring never overflows. This
         * is OUTSIDE the timed region (all samples already recorded), so it does NOT affect
         * measurements; it only adds ~ (n/16) ms of transfer time per round (e.g. 1000
         * samples -> ~63 ms), which is negligible against multi-second compute rounds. */
        k_sleep(K_MSEC(1));
    }
    MCU_EMIT("RAWEND round=%ld group=%s n=%ld", r, label, n);
}

/* ---- SINGLE-ROUND runner (orchestrator-driven, design "b") ----
 * Runs EXACTLY ONE round: warmup (every round, per decision) + count timed ops, then emits
 *   (1) the ROUND summary line (doubles as the laptop's integrity cross-check: the laptop
 *       recomputes min/max/mean from the raw dump and compares -- min/max STRICT, mean eps),
 *   (2) the full RAW sample dump for that round.
 * The caller (orchestrator) issues one of these per round, validates, retries if needed,
 * and only advances when clean. The MCU never holds more than ONE round of samples. */
static void mcu_run_one_round(const char *label, int unit_is_ms, long round_idx,
                              long count, long warmup,
                              mcu_op_fn op, void *ctx,
                              uint64_t *scratch /* size >= count */,
                              mcu_reset_fn reset /* may be NULL */)
{
    if (count > 0 && reset && reset(ctx) != 0) {
        MCU_EMIT("ROUND %ld group=%s ok=0 RESET_FAILED", round_idx, label);
        return;
    }
    /* warmup EVERY round (decision): not timed, not stored, not dumped.
     * The yield clock spans warmup AND the timed loop, so the no-yield window can never exceed
     * MCU_YIELD_MAX_MS no matter how the ops split between the two (this was the hz3 p521 bug). */
    mcu_yield_reset();
    for (long i=0;i<warmup;i++){ uint64_t t; op(ctx,&t); mcu_maybe_yield(i); }

    long n=0, discarded=0, failed=0;
    for (long i=0;i<count;i++){
        uint64_t t;
        int rc = op(ctx,&t);
        if (rc == 0)        scratch[n++]=t;
        else if (rc == -2)  discarded++;
        else                failed++;
        mcu_maybe_yield(i);                            /* feed lower-prio threads */
    }
    k_sleep(K_MSEC(2));

    if (n==0){
        MCU_EMIT("ROUND %ld group=%s mode=iters ok=0 ALL_OPS_FAILED discarded=%ld failed=%ld",
                 round_idx, label, discarded, failed);
        return;
    }
    if (discarded || failed){
        MCU_EMIT("WARN round=%ld group=%s kept=%ld of %ld discarded=%ld(>=25s) failed=%ld",
                 round_idx, label, n, count, discarded, failed);
    }
    /* per-round summary (integrity cross-check source). Welford mean/sd in ns. */
    double mean=0,M2=0;
    for (long i=0;i<n;i++){ double d=(double)scratch[i]-mean; mean+=d/(i+1); M2+=d*((double)scratch[i]-mean); }
    double sd = n>1 ? sqrt(M2/(n-1)) : 0.0;
    qsort(scratch,n,sizeof(uint64_t),_mcu_cmp_u64);
    double mn=scratch[0], mx=scratch[n-1];
    double med=scratch[n/2], p95=scratch[(long)(n*0.95)];
    double div = unit_is_ms ? 1e6 : 1e3;
    if (unit_is_ms){
        MCU_EMIT("ROUND %ld group=%s mode=iters handshakes=%ld "
                 "ms_mean=%.4f ms_sd=%.4f ms_min=%.4f ms_med=%.4f ms_p95=%.4f ms_max=%.4f",
                 round_idx,label,n, mean/div,sd/div,mn/div,med/div,p95/div,mx/div);
    } else {
        MCU_EMIT("ROUND %ld group=%s mode=iters iters=%ld "
                 "us_mean=%.4f us_sd=%.4f us_min=%.4f us_med=%.4f us_p95=%.4f us_max=%.4f",
                 round_idx,label,n, mean/div,sd/div,mn/div,med/div,p95/div,mx/div);
    }
    /* raw dump AFTER the summary, then the caller advances and the buffer is reused */
    mcu_dump_raw(label, round_idx, scratch, n);
    MCU_EMIT("ROUNDDONE round=%ld group=%s n=%ld", round_idx, label, n);
}

/* Energy/time mode: run for window_ms wall-clock, count ops, emit a PAIR_WITH_METER
 * line so an external meter reading (mAh over the same window) maps to op count.
 * Mirrors the Pi harness PAIR_WITH_METER semantics. */
static void mcu_run_time(const char *label, int unit_is_ms,
                         long window_ms, long warmup,
                         mcu_op_fn op, void *ctx)
{
    mcu_yield_reset();
    for (long i=0;i<warmup;i++){ uint64_t t; op(ctx,&t); mcu_maybe_yield(i); }
    uint64_t t0=mcu_now_us(); long ok=0,att=0;
    uint64_t win_us=(uint64_t)window_ms*1000ull;
    /* LATENT BUG FIXED: this loop never yielded, so any energy window longer than the RP2350
     * no-yield threshold (~25 s) would have wedged the board. Time-only yield: 1 ms per
     * MCU_YIELD_MAX_MS keeps idle time ~0.01% so energy/op is not disturbed. */
    mcu_yield_reset();
    while (mcu_now_us()-t0 < win_us){ uint64_t t; if(op(ctx,&t)==0) ok++; att++; mcu_maybe_yield_time(); }
    double el_s=(mcu_now_us()-t0)/1e6;
    MCU_EMIT("ROUND 0 group=%s mode=time elapsed_s=%.4f attempts=%ld ok=%ld ops_per_s=%.3f %s=%.4f",
             label,el_s,att,ok, ok/el_s,
             unit_is_ms?"ms_per_op":"us_per_op",
             unit_is_ms ? (ok?el_s/ok*1e3:0.0) : (ok?el_s/ok*1e6:0.0));
    MCU_EMIT("PAIR_WITH_METER round=0 window_s=%.4f ops=%ld (read mAh on the meter)", el_s, ok);
}

#endif /* MCU_MEASURE_CORE_H */
