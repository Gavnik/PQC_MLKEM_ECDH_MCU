#!/usr/bin/env python3
# pqc_sweep.py -- Layer-1 sweep driver for the PQC MCU benchmark.
#
# PROTOCOL (per-round stream-and-store, matching the firmware's run1/mcu_run_one_round):
#   The MCU runs ONE round (cycle-counter timed per op, interrupts NOT masked), then -- measurement halted -- streams
#   that round's raw per-op nanoseconds to the PC (RAWSTART/SAMPLES/RAWEND) and reuses its single
#   round buffer. The driver stores each round AS IT ARRIVES (flushed to a sidecar) and only then
#   lets the MCU advance to the next round. Each op's CSV row is written+flushed the moment the
#   op's rounds finish -- nothing is held in memory to the end, so an interrupt/wedged op never
#   costs already-collected data.
#
#   Per op, two passes (kept separate so timing stays pristine):
#     * timing:  run1 <phase> <lv> <r> <iters> <warmup>  for r in 0..rounds-1   (RAW per-op dump)
#     * memory:  run  <phase> <lv> mem <iters> <rounds>                          (stack/heap + SIZES)
#
#   TIMING STATS computed from the RAW samples -> every statistic exact over all iters of all
#   rounds (incl. median/p95, which per-round summaries cannot give):
#     time_mean_us / time_sd_us      pooled over ALL iters of ALL rounds
#     time_median_us / time_p95_us   exact pooled percentiles (firmware nearest-rank: sorted[int(N*q)])
#     time_min_us / time_max_us      exact pooled min / max
#     time_round_sd_us               sample SD BETWEEN the per-round means (reproducibility)
#   Integrity: per round, collected count must equal RAWEND n (else retry); min/max/mean are
#   cross-checked vs the firmware ROUND summary (warn-only).
#
# GATES (refuses to measure): firmware without the c25519/harden/kemkeys/wx self-report,
#   asm=partial, wx not in {mpu,pmp,pms} (memory protection not live), or opt != split-O2. Each row records the
#   self-report (board, arch, placement, asm_state, c25519_impl, ecc_harden, kem_keys, wx, opt,
#   tmr_hz).
#   Exception (2026-11-12): --control-wx-off accepts ONLY wx=off, into a file whose name contains "wxoff" (W^X-off control).
#   ML-KEM encaps/decaps: KEYFP of every round is checked against kem_seed_fingerprints.txt
#   (independent FIPS 203 implementation) and MISMATCH is printed loudly.
#
# USAGE
#   pilot (defaults): python3 pqc_sweep.py --port <serial> --out pilot.csv
#                     = 3 rounds x 100 iters, warmup 3; memory pass 3 x 100
#   final:            python3 pqc_sweep.py --port <serial> --out final.csv --rounds 100
#   Rows append to --out; raw per-round samples go to <out>.raw. Needs: pip install pyserial
#   Run scripts/fault_check.py on the same image first (W^X proof per image).

import argparse, csv, math, os, re, sys, time
from datetime import datetime, timezone

try:
    import serial  # pyserial
except ImportError:
    sys.exit("pyserial not installed: pip install pyserial")

MLKEM_LEVELS = ["512", "768", "1024"]
MLKEM_PHASES = ["keygen", "encaps", "decaps"]
ECDH_CURVES  = ["x25519", "p256", "p384", "p521"]
ECDH_PHASES  = ["keygen", "derive"]

def op_matrix():
    ops = []
    for lv in MLKEM_LEVELS:
        for ph in MLKEM_PHASES:
            ops.append(("mlkem", "mlkem" + lv, ph, lv))
    for cv in ECDH_CURVES:
        for ph in ECDH_PHASES:
            ops.append(("ecdh", cv, ph, cv))
    return ops  # (family, algo, phase, lv_token)

CSV_COLUMNS = [
    "board", "arch", "placement", "asm_state", "c25519_impl", "ecc_harden", "kem_keys", "wx", "opt", "family", "algo", "phase",
    "iters", "rounds", "warmup",
    "time_mean_us", "time_sd_us", "time_round_sd_us",
    "time_median_us", "time_p95_us", "time_min_us", "time_max_us",
    "stack_peak_kb", "heap_peak_kb", "working_kb",
    "pk_bytes", "sk_bytes", "ct_bytes", "ss_bytes",
    "flash_bytes", "ram_bytes", "fw_build", "tmr_hz", "timestamp",
]

# ---- serial helpers --------------------------------------------------------------------------
def kv(line):
    return dict(re.findall(r"(\w+)=([^\s]+)", line))

def read_until(ser, terminator, timeout):
    lines, buf, deadline = [], "", time.time() + timeout
    while time.time() < deadline:
        chunk = ser.read(512)
        if chunk:
            buf += chunk.decode("utf-8", "replace")
            while "\n" in buf:
                raw, buf = buf.split("\n", 1)
                line = raw.strip("\r\n ")
                if line:
                    lines.append(line)
                    if line.startswith(terminator):
                        return lines
        else:
            time.sleep(0.01)
    return lines

def send(ser, cmd):
    ser.reset_input_buffer()
    ser.write((cmd + "\r\n").encode())
    ser.flush()

def query_id(ser, retries=5):
    for _ in range(retries):
        send(ser, "id")
        for line in read_until(ser, "ID", 5.0):
            if line.startswith(("ID", "SELFREPORT")):
                d = kv(line)
                if "board" in d and "asm" in d:
                    return d   # board, arch, asm, placement, c25519, tmr_hz (self-reported)
        time.sleep(0.3)
    return None

# ---- exact stats over RAW samples ------------------------------------------------------------
def pct(sorted_ns, q):
    n = len(sorted_ns)
    return sorted_ns[min(n - 1, int(n * q))]  # firmware nearest-rank convention

def stats_from_raw(samples_ns, round_means_ns):
    n = len(samples_ns)
    mean = sum(samples_ns) / n
    var = sum((x - mean) ** 2 for x in samples_ns) / (n - 1) if n > 1 else 0.0
    sd = math.sqrt(var)
    s = sorted(samples_ns)
    if len(round_means_ns) > 1:
        mbar = sum(round_means_ns) / len(round_means_ns)
        rsd = math.sqrt(sum((m - mbar) ** 2 for m in round_means_ns) / (len(round_means_ns) - 1))
    else:
        rsd = 0.0
    u = 1000.0  # ns -> us
    return {"mean": mean / u, "sd": sd / u, "round_sd": rsd / u,
            "median": s[n // 2] / u, "p95": pct(s, 0.95) / u, "min": s[0] / u, "max": s[-1] / u}

# ---- passes ----------------------------------------------------------------------------------
def load_kem_ref():
    """Expected ML-KEM key fingerprints per (algo, phase, round), computed off-board with an
    independent FIPS 203 implementation from the same pre-shared seeds (kem_seed_fingerprints.txt)."""
    ref, path = {}, os.path.join(os.path.dirname(os.path.abspath(__file__)), "kem_seed_fingerprints.txt")
    if not os.path.exists(path):
        sys.exit(f"Missing {path}: the ML-KEM key-fingerprint reference must sit next to pqc_sweep.py "
                 "(without it the identical-keys check would be silently skipped).")
    for line in open(path):
        if line.strip() and not line.startswith("#"):
            a, p, r, fp = line.split()
            ref[(a, p, int(r))] = fp
    if len(ref) != 600:
        sys.exit(f"{path}: expected 600 reference fingerprints, found {len(ref)}")
    return ref

def run_timing(ser, phase, lv, count, rounds, warmup, raw_sink=None, retries=3):
    """One run1 per round; each validated round handed to raw_sink(r, samples) BEFORE advancing."""
    all_samples, round_means = [], []
    for r in range(rounds):
        for _ in range(retries):
            send(ser, f"run1 {phase} {lv} {r} {count} {warmup}")
            lines = read_until(ser, "END1", 240.0)
            samples, summ, raw_n, fp = [], None, None, "-"
            for line in lines:
                if line.startswith("KEYFP"):
                    fp = kv(line).get("fp", "?")
                elif line.startswith("SAMPLES"):
                    samples += [int(x) for x in line.split()[1:]]
                elif line.startswith("ROUND ") and "us_mean" in line:
                    summ = kv(line)
                elif line.startswith("RAWEND"):
                    raw_n = int(kv(line).get("n", "-1"))
            if samples and raw_n == len(samples):
                if summ:
                    mn, mx = min(samples) / 1000, max(samples) / 1000
                    me = (sum(samples) / len(samples)) / 1000
                    if (abs(mn - float(summ.get("us_min", mn))) > 0.01 or
                        abs(mx - float(summ.get("us_max", mx))) > 0.01 or
                        abs(me - float(summ.get("us_mean", me))) > 1.0):
                        print(f"  [warn] {phase} {lv} r{r}: raw vs summary mismatch (accepted)")
                if raw_sink:
                    raw_sink(r, samples, fp)    # persist this round NOW, before advancing
                all_samples += samples
                round_means.append(sum(samples) / len(samples))
                break
            print(f"  [retry] {phase} {lv} r{r}: got {len(samples)} samples, RAWEND n={raw_n}")
        else:
            return None
    return all_samples, round_means

def run_mem(ser, phase, lv, count, rounds):
    send(ser, f"run {phase} {lv} mem {count} {rounds}")
    stack = heap = working = float("nan")
    sizes = {"pk": "", "sk": "", "ct": "", "ss": ""}
    for line in read_until(ser, "END", 240.0):
        d = kv(line)
        if line.startswith("AGG") and d.get("mode") == "mem":
            stack, heap, working = (float(d.get(k, "nan")) for k in
                                    ("net_op_stack_KB", "heap_peak_KB", "working_KB"))
        elif line.startswith("SIZES"):
            for k in sizes:
                if k in d:
                    sizes[k] = d[k]
    return stack, heap, working, sizes

def main():
    ap = argparse.ArgumentParser(description="Layer-1 PQC sweep driver -> CSV (exact raw stats, incremental persist)")
    ap.add_argument("--port", required=True)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--out", default="sweep.csv")
    ap.add_argument("--iters", type=int, default=100)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--warmup", type=int, default=3)
    ap.add_argument("--mem-iters", type=int, default=100,
                    help="iterations per round of the memory pass (stack/heap peaks are identical every run, "
                         "so this stays small even when --iters is large)")
    ap.add_argument("--mem-rounds", type=int, default=3)
    ap.add_argument("--flash-bytes", default="")
    ap.add_argument("--ram-bytes", default="")
    ap.add_argument("--fw-build", default="")
    ap.add_argument("--control-wx-off", action="store_true",
                    help="W^X-off CONTROL run (2026-11-12): accept only firmware reporting wx=off; the output "
                         "file name must contain 'wxoff' so control rows never mix with the measured dataset")
    ap.add_argument("--ops", default="",
                    help="comma list of algo:phase to run a subset, e.g. 'p521:derive,mlkem768:keygen' "
                         "(default: the full 17-op matrix)")
    args = ap.parse_args()

    ops = op_matrix()
    if args.ops:
        want = {tuple(tok.split(":", 1)) for tok in args.ops.split(",") if ":" in tok}
        ops = [o for o in ops if (o[1], o[2]) in want]   # match (algo, phase)
        if not ops:
            sys.exit(f"--ops '{args.ops}' matched nothing; use algo:phase like p521:derive")

    ser = serial.Serial(args.port, args.baud, timeout=0.2)
    time.sleep(0.5)

    ident = query_id(ser)
    if not ident:
        sys.exit("Could not read the firmware `id` self-report -- booted? correct port?")
    board, arch = ident.get("board", "?"), ident.get("arch", "?")
    asm, placement = ident.get("asm", "?"), ident.get("placement", "?")
    c25519, tmr_hz = ident.get("c25519", "?"), ident.get("tmr_hz", "")
    harden = ident.get("harden", "?")
    kemkeys = ident.get("kemkeys", "?")
    wx = ident.get("wx", "?")
    opt = ident.get("opt", "?")
    if opt != "split-O2":
        sys.exit(f"Firmware reports opt={opt}: every measured image must be built with the optimisation "
                 "split (wolfSSL + main.c -O2, Zephyr -Os; cmake/opt_split.cmake + prj.conf). Rebuild.")
    if wx == "?":
        sys.exit("Firmware self-report lacks wx= -- flash firmware built from the current main.c.")
    if args.control_wx_off:
        if wx != "off":
            sys.exit(f"--control-wx-off: firmware reports wx={wx}, expected wx=off (build it with W^X disabled).")
        if "wxoff" not in os.path.basename(args.out):
            sys.exit("--control-wx-off: the --out file name must contain 'wxoff' (keeps control rows apart).")
    elif wx not in ("mpu", "pmp", "pms"):
        sys.exit(f"Firmware reports wx={wx}: memory protection is NOT live. Every measured image must run "
                 "with W^X on (Arm: Zephyr MPU via the board conf / overlay-mpu-on / overlay-hyb-arm; "
                 "hz3/ESP32: CONFIG_PQC_WX=y in the board conf). Check the boot log (WX(...) lines).")
    if kemkeys == "?":
        sys.exit("Firmware self-report lacks kemkeys= -- flash firmware built from the current main.c.")
    if asm == "partial":
        sys.exit("Firmware reports asm=partial (SP-ECC asm and Thumb-2 asm not both on) -- config error.")
    if c25519 == "?" or harden == "?":
        sys.exit("Firmware self-report lacks c25519=/harden= -- flash firmware built from the current main.c.")
    print(f"[sweep] board={board} arch={arch} asm={asm} placement={placement} "
          f"c25519={c25519} harden={harden} kemkeys={kemkeys} wx={wx} opt={opt} tmr_hz={tmr_hz}")

    # open CSV (append; header once) and the raw sidecar -- both persisted incrementally
    new = not os.path.exists(args.out) or os.path.getsize(args.out) == 0
    if not new:
        with open(args.out, newline="") as f:
            hdr = next(csv.reader(f), [])
        if hdr != CSV_COLUMNS:
            sys.exit(f"{args.out} has an older column layout. Run once:\n"
                     f"  python3 csv_migrate.py {args.out}\nthen re-run this command.")
    csv_f = open(args.out, "a", newline="")
    w = csv.DictWriter(csv_f, fieldnames=CSV_COLUMNS)
    if new:
        w.writeheader(); csv_f.flush()
    raw_f = open(args.out + ".raw", "a")

    kem_ref = load_kem_ref()
    fp_bad = []
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    done = 0
    for family, algo, phase, lv in ops:
        print(f"[sweep] {algo} {phase} ...", flush=True)
        def sink(r, samples, fp, _a=algo, _p=phase):
            exp = kem_ref.get((_a, _p, r))
            if exp is not None and fp != exp:
                fp_bad.append(f"{_a} {_p} r{r}: board {fp} expected {exp}")
                print(f"  [KEY MISMATCH] {_a} {_p} r{r}: board fp {fp} != expected {exp}")
            raw_f.write(f"{board} {arch} {asm} {placement} c25519={c25519} harden={harden} wx={wx} opt={opt} {_a} {_p} r{r} fp={fp} n={len(samples)} "
                        + " ".join(map(str, samples)) + "\n")
            raw_f.flush()
        tr = run_timing(ser, phase, lv, args.iters, args.rounds, args.warmup, raw_sink=sink)
        if tr is None:
            print(f"  [skip] {algo} {phase}: timing failed after retries")
            continue
        st = stats_from_raw(tr[0], tr[1])
        stack, heap, working, sizes = run_mem(ser, phase, lv, args.mem_iters, args.mem_rounds)
        print(f"  mean {st['mean']:.1f}us sd {st['sd']:.1f} med {st['median']:.1f} "
              f"p95 {st['p95']:.1f} round-sd {st['round_sd']:.2f}  stack {stack:.2f}KB")
        w.writerow({
            "board": board, "arch": arch, "placement": placement, "asm_state": asm,
            "c25519_impl": c25519, "ecc_harden": harden, "kem_keys": kemkeys, "wx": wx, "opt": opt,
            "family": family, "algo": algo, "phase": phase,
            "iters": args.iters, "rounds": args.rounds, "warmup": args.warmup,
            "time_mean_us": f"{st['mean']:.4f}", "time_sd_us": f"{st['sd']:.4f}",
            "time_round_sd_us": f"{st['round_sd']:.4f}",
            "time_median_us": f"{st['median']:.4f}", "time_p95_us": f"{st['p95']:.4f}",
            "time_min_us": f"{st['min']:.4f}", "time_max_us": f"{st['max']:.4f}",
            "stack_peak_kb": f"{stack:.2f}", "heap_peak_kb": f"{heap:.2f}", "working_kb": f"{working:.2f}",
            "pk_bytes": sizes["pk"], "sk_bytes": sizes["sk"], "ct_bytes": sizes["ct"], "ss_bytes": sizes["ss"],
            "flash_bytes": args.flash_bytes, "ram_bytes": args.ram_bytes,
            "fw_build": args.fw_build, "tmr_hz": tmr_hz, "timestamp": ts,
        })
        csv_f.flush()          # row on disk the instant the op finishes
        done += 1

    csv_f.close(); raw_f.close(); ser.close()
    print(f"[sweep] wrote {done} op rows to {args.out} (+ raw samples in {args.out}.raw)")
    if kem_ref:
        print(f"[sweep] ML-KEM key fingerprints: " + ("ALL MATCH the pre-shared-seed reference" if not fp_bad
              else f"{len(fp_bad)} MISMATCH(ES) -- boards did NOT all use the same keys:\n  " + "\n  ".join(fp_bad)))

if __name__ == "__main__":
    main()
