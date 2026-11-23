#!/usr/bin/env python3
"""memtopo_check.py -- verdict for the m4 'memtopo' test (2026-11-11).

Usage:  python3 memtopo_check.py diag_memtopo_m4.txt

Reads the MEMTOPO lines printed by the m4 diagnostic flash image (build-m4-fetch, command 'memtopo'),
predicts every row under four candidate causes, and says which one the board follows.

Model: each kernel loop runs 66 instructions (64 + subs + bne.w). The Cortex-M4 fetches whole
32-bit words (Cortex-M4 TRM 2.3.1), so a loop needs W fetch words (16-bit kernels 134 B = 33.5,
32-bit kernels 262 B = 65.5) and D data accesses (64 for ld/st, 0 for alu). A path carries one
transfer per cycle. If fetches and data use the same path, a loop takes W + D cycles, otherwise
max(66, W) and max(66, D). One cycle per loop is added for the taken branch.

  H1 slow SRAM          every SRAM transfer takes 2 cycles; separate paths everywhere
  H2 one merged port    fetches and data share one path for every target (flash and SRAM)
  H3 per-bank conflict  fetches and data collide only inside the same SRAM bank (or both in flash)
  H4 one SRAM path      all CM4 SRAM traffic (any bank) shares one path; flash has its own

Rows where code and data sit in the same SRAM bank are left out of the score for stores (a store
to the bank the code is fetched from costs extra inside the SRAM controller); they are printed as
the bank check instead.
"""
import re
import sys

INSTR = 66
W = {"16": 134 / 4, "32": 262 / 4}


def path(where, hyp):
    if where in ("none",):
        return None
    if hyp == "H2":
        return "P"
    if where == "flash":
        return "F"
    if hyp == "H3":
        return where           # SRAM0 / SRAM1 / SRAM2 are separate
    return "S"                 # H1, H4: one SRAM path


def predict(kernel, code, data, hyp):
    kind, width = kernel[:-2], kernel[-2:]
    w = W[width]
    d = 0 if kind == "alu" else 64
    fetch_cost = 2 if (hyp == "H1" and code != "flash") else 1
    data_cost = 2 if (hyp == "H1" and data not in ("flash", "none")) else 1
    fc, dc = w * fetch_cost, d * data_cost
    pc, pd = path(code, hyp), path(data, hyp)
    if d and hyp != "H1" and pc == pd:
        cyc = fc + dc
    else:
        cyc = max(INSTR, fc, dc)
    return (cyc + 1) / INSTR


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    rows, cfg = [], []
    for line in open(sys.argv[1], errors="replace"):
        if "MEMTOPO cfg" in line or "MEMTOPO image" in line or "MEMTOPO using" in line or "MEMTOPO refused" in line:
            cfg.append(line.strip()[line.find("MEMTOPO"):])
        m = re.search(r"MEMTOPO kernel=(\w+) code=(\w+) data=(\w+) .* cyc=(\d+) cpi_x1000=(\d+)", line)
        if m:
            rows.append((m.group(1), m.group(2), m.group(3), int(m.group(5)) / 1000))
    print("== configuration read back from the chip ==")
    flags = []
    for c in cfg:
        print("  " + c)
        for key, bad in (("FAST_WS", lambda v: v != 0), ("FAST_INT_DIV", lambda v: v != 0),
                         ("CA_EN", lambda v: v != 1), ("PREF_EN", lambda v: v != 1)):
            for v in re.findall(key + r"=(\d+)", c):
                if bad(int(v)):
                    flags.append(f"{key}={v} in: {c}")
    if not rows:
        sys.exit("no MEMTOPO kernel lines found")

    hyps = ["H1", "H2", "H3", "H4"]
    print("\n== measured cycles per instruction vs each cause ==")
    print(f"{'kernel':6} {'code':6} {'data':6} {'meas':>6} " + " ".join(f"{h:>6}" for h in hyps))
    err = {h: [] for h in hyps}
    bank = []
    for k, c, d, cpi in rows:
        p = {h: predict(k, c, d, h) for h in hyps}
        same_bank_store = k.startswith("st") and c == d and c.startswith("SRAM")
        mark = "  (bank check, not scored)" if same_bank_store else ""
        print(f"{k:6} {c:6} {d:6} {cpi:6.3f} " + " ".join(f"{p[h]:6.2f}" for h in hyps) + mark)
        if same_bank_store:
            bank.append((k, c, cpi))
            continue
        for h in hyps:
            err[h].append(abs(cpi - p[h]))

    print("\n== fit (|measured - predicted|, cycles per instruction) ==")
    for h in hyps:
        e = err[h]
        print(f"  {h}: max {max(e):.3f}  mean {sum(e) / len(e):.3f}  rows within 0.10: {sum(x <= 0.10 for x in e)}/{len(e)}")
    best = min(hyps, key=lambda h: max(err[h]))
    others = [h for h in hyps if h != best]
    ok = max(err[best]) <= 0.10 and all(max(err[h]) >= 0.30 for h in others)

    print("\n== bank check (stores into the bank the code runs from) ==")
    cross = {}
    for k, c, d, cpi in rows:
        if k.startswith("st") and c.startswith("SRAM") and d.startswith("SRAM") and c != d:
            cross.setdefault((k, c), []).append(cpi)
    for k, c, cpi in bank:
        other = cross.get((k, c), [])
        if other:
            print(f"  {k} code={c}: same bank {cpi:.3f} vs other banks {min(other):.3f}-{max(other):.3f}"
                  + ("  -> banks are where we think" if cpi > max(other) + 0.05 else "  -> NO difference: bank map unconfirmed"))

    print("\n== verdict ==")
    if flags:
        print("  CONFIGURATION FLAG(S): " + "; ".join(flags))
    else:
        print("  configuration: no SRAM wait state on the CM4 side, no clk_fast divider, flash cache and prefetch on")
    names = {"H1": "slow SRAM (wait states)", "H2": "one merged port for every target",
             "H3": "conflict inside one SRAM bank", "H4": "one shared CM4 path to all SRAM; flash on its own path"}
    if ok:
        print(f"  CAUSE: {best} = {names[best]} (max error {max(err[best]):.3f}; every other cause misses by >= 0.30)")
    else:
        print(f"  NO SINGLE CAUSE fits cleanly (best {best}, max error {max(err[best]):.3f}) -- send the log")


if __name__ == "__main__":
    main()
