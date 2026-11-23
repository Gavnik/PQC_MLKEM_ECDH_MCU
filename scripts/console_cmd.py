#!/usr/bin/env python3
"""
console_cmd.py -- send console commands to a booted board and save everything it prints.

  python3 console_cmd.py --port <port> --out <file.txt> --cmd mpu --cmd fetchtest [--until "FETCHTEST done"]

Each --cmd is sent in order; after each one the script reads until no new line has arrived for
--quiet seconds (default 3), or until a line contains --until (then it moves to the next command).
All lines are printed and written to --out.
"""
import argparse, time
import serial  # pyserial

ap = argparse.ArgumentParser()
ap.add_argument("--port", required=True)
ap.add_argument("--baud", type=int, default=115200)
ap.add_argument("--out", required=True)
ap.add_argument("--cmd", action="append", required=True)
ap.add_argument("--until", default="", help="stop reading a command's output at a line containing this")
ap.add_argument("--quiet", type=float, default=3.0)
ap.add_argument("--timeout", type=float, default=240.0, help="max seconds per command")
a = ap.parse_args()

s = serial.Serial(a.port, a.baud, timeout=0.2)
time.sleep(1.0)
s.reset_input_buffer()
out = []
for c in a.cmd:
    s.write(b"\r\n" + c.encode() + b"\r\n"); s.flush()
    t0 = last = time.time(); buf = b""
    while time.time() - t0 < a.timeout and time.time() - last < a.quiet:
        chunk = s.read(256)
        if not chunk:
            continue
        last = time.time(); buf += chunk
        done = False
        while b"\n" in buf:
            ln, buf = buf.split(b"\n", 1)
            ln = ln.decode(errors="replace").rstrip()
            print(ln); out.append(ln)
            if a.until and a.until in ln:
                done = True
        if done:
            break
s.close()
open(a.out, "w").write("\n".join(out) + "\n")
print(f"[saved {len(out)} lines to {a.out}]")
