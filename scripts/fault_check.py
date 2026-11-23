#!/usr/bin/env python3
"""
fault_check.py -- run the W^X fault-injection tests on ONE flashed image and append the results to
a CSV (one row per test), so every final image has recorded proof that its protection is live.

Per image it runs:
  faultw  store into code               (hybrid: the SRAM crypto code block; flash: flash code)
  faultx  call into an SRAM data buffer (data must not execute)
  faultt  store into the crypto tables  (hybrid only)
  faulttx call into the crypto tables   (hybrid only; proves the tables are no-exec)
  faultwend / faulttend / faulttxend  the same at the LAST word of each block (hybrid, every core;
          on hz3 the last word lies in the block's second PMP tile)

How a test ends, per core (all of it is printed by main.c):
  m33 / m4 / hz3 : the fault reboots the board; the next boot prints
                   "LAST-FAULT kind=... " + "LAST-FAULT verdict: W^X VIOLATION CAUGHT (PASS)".
                   USB-CDC (m33, hz3) re-enumerates: the script reopens the port.
  ESP32          : a blocked access raises no exception by itself; the PMS MONITOR records it.
                   Stores: "write BLOCKED ... recorded by the PMS" -> PASS. Exec tests (a call
                   through the instruction-bus alias) crash on the non-instruction the blocked
                   fetch returns; the fatal handler then prints the monitor record and
                   "LAST-FAULT verdict: ... (PASS)" (fetch violation at the target). The script
                   then resets the board through RTS.
  any core       : "NO FAULT" / "write LANDED" / "RETURNED" / "UNEXPECTED" -> FAIL.

Usage (board must be booted with the image under test, console free):
  python3 fault_check.py --port /dev/ttyACM0 --out faults_final.csv
  python3 fault_check.py --port $LX7PORT --out faults_final.csv
"""
import argparse, csv, datetime, os, re, sys, time
import serial  # pyserial

COLS = ["timestamp", "board", "arch", "placement", "asm_state", "c25519_impl", "wx",
        "test", "result", "evidence"]
EXPECT_KIND = {"faultw": ("write-RO", "write-code"), "faultwend": ("write-RO", "write-code"),
               "faultx": ("exec-XN", "exec-data"),
               "faultt": ("write-tables",), "faulttend": ("write-tables",),
               "faulttx": ("exec-tables",), "faulttxend": ("exec-tables",)}


class Con:
    def __init__(self, port, baud):
        self.port, self.baud, self.s = port, baud, None
        self.open(10)

    def open(self, wait_s):
        t0 = time.time()
        while time.time() - t0 < wait_s:
            try:
                self.s = serial.Serial(self.port, self.baud, timeout=0.2)
                return True
            except (serial.SerialException, OSError):
                time.sleep(0.5)
        return False

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass
        self.s = None

    def send(self, cmd):
        self.s.write((cmd + "\r\n").encode()); self.s.flush()

    def lines(self, timeout_s, reconnect=True):
        """Yield decoded lines until timeout; survives the port vanishing (USB reboot)."""
        t0, buf = time.time(), b""
        while time.time() - t0 < timeout_s:
            try:
                chunk = self.s.read(256) if self.s else b""
            except (serial.SerialException, OSError):
                self.close()
                if not reconnect:
                    return
                yield "<<port lost: board rebooting>>"
                time.sleep(1.0)
                if not self.open(30):
                    yield "<<port did not come back in 30 s>>"
                    return
                yield "<<port reopened>>"
                continue
            if not chunk:
                continue
            buf += chunk
            while b"\n" in buf:
                ln, buf = buf.split(b"\n", 1)
                yield ln.decode(errors="replace").strip()

    def esp_reset(self):
        """Classic ESP auto-reset circuit: EN low via RTS with DTR (IO0) high -> normal boot."""
        self.s.dtr = False; self.s.rts = True; time.sleep(0.15); self.s.rts = False


def kv(line):
    return dict(re.findall(r"(\w+)=(\S+)", line))


def get_id(con):
    for _ in range(3):
        con.send(""); con.send("id")
        for ln in con.lines(4, reconnect=False):
            if ln.startswith("ID "):
                return kv(ln)
    sys.exit("No ID line -- is the board booted and the console free (close picocom)?")


def wait_ready(con, timeout_s=25):
    for ln in con.lines(timeout_s):
        if ln.startswith("READY"):
            time.sleep(1.5)
            return True
    return False


def run_test(con, test, ident):
    is_esp = ident.get("arch") == "xtensa"
    con.send(test)
    ev, result, crashed = [], None, False
    for ln in con.lines(40):
        if not ln:
            continue
        if ln.startswith(("FAULT", "LAST-FAULT", " **", "<<")):
            ev.append(ln)
        if "UNEXPECTED" in ln and not ln.startswith("LAST-FAULT verdict:"):
            result = "FAIL"; break
        if "write BLOCKED" in ln:
            result = "PASS"; break
        if "NO FAULT" in ln or "write LANDED" in ln or "RETURNED" in ln or "n/a" in ln or "refused" in ln:
            result = "N/A" if "n/a" in ln else "FAIL"; break
        if is_esp and "EXCCAUSE" in ln:
            crashed = True       # ESP32: the verdict follows the dump (PMS monitor, fatal handler)
        if ln.startswith("LAST-FAULT kind="):
            kind = kv(ln).get("kind", "?")
            if kind not in EXPECT_KIND[test]:
                result = f"FAIL(kind={kind})"
        if ln.startswith("LAST-FAULT verdict:"):
            if result is None:
                result = "PASS" if "(PASS)" in ln else "FAIL"
            for _ in con.lines(1.0, reconnect=False):   # rest of the report
                pass
            break
    if is_esp and crashed:       # the ESP32 halts in its fatal handler: reset it for the next test
        con.esp_reset()
        wait_ready(con)
        if result is None:
            result = "FAIL(no PMS verdict after the crash)"
    if result is None:
        result = "NO-REPORT"   # e.g. USB boot lines lost; rerun this one test
    return result, " | ".join(ev)[:600]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", required=True)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--out", default="faults_final.csv")
    ap.add_argument("--tests", default="", help="comma list to override the default set")
    a = ap.parse_args()

    con = Con(a.port, a.baud)
    ident = get_id(con)
    print("ID:", " ".join(f"{k}={ident.get(k, '?')}" for k in
                          ("board", "arch", "asm", "placement", "c25519", "wx", "opt")))
    if ident.get("wx") not in ("mpu", "pmp", "pms"):
        sys.exit(f"wx={ident.get('wx')} -- protection is not live on this image; nothing to test")
    hyb = ident.get("placement", "").startswith("hybrid")
    split = ident.get("placement") in ("hybrid-code", "hybrid-tables")   # diagnostic m4 images
    tests = ["faultw", "faultx"]
    if hyb:   # the same set on every core (2026-11-11)
        tests += ["faultt", "faulttx", "faultwend", "faulttend", "faulttxend"]
    if a.tests:
        tests = [t.strip() for t in a.tests.split(",") if t.strip()]

    new = not os.path.exists(a.out)
    with open(a.out, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        if new:
            w.writeheader()
        allpass = True
        for t in tests:
            res, ev = run_test(con, t, ident)
            allpass &= (res == "PASS") or (split and res == "N/A")   # split images lack one block
            print(f"  {t:10s} {res:10s} {ev[:160]}")
            w.writerow({"timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
                        "board": ident.get("board"), "arch": ident.get("arch"),
                        "placement": ident.get("placement"), "asm_state": ident.get("asm"),
                        "c25519_impl": ident.get("c25519"), "wx": ident.get("wx"),
                        "test": t, "result": res, "evidence": ev})
            f.flush()
            time.sleep(1.0)
            ident2 = get_id(con)                       # protection must be back after the reboot
            if ident2.get("wx") != ident.get("wx"):
                print(f"  !! after {t}: wx={ident2.get('wx')} (expected {ident.get('wx')})")
                allpass = False
    con.close()
    print("ALL PASS" if allpass else "SOME TESTS DID NOT PASS -- see the CSV")
    sys.exit(0 if allpass else 1)


if __name__ == "__main__":
    main()
