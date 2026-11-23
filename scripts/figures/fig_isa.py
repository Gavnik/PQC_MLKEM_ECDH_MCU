"""fig_isa.py -- regenerates fig4_isa.pdf/.png in figures/ from data/summary/layer1_results_v7.xlsx."""
import openpyxl
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
XLSX = ROOT / "data" / "summary" / "layer1_results_v7.xlsx"
OUT = ROOT / "figures"
OUT.mkdir(exist_ok=True)
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

wb = openpyxl.load_workbook(XLSX, data_only=True)
D = {}
for r in wb['Data_v7'].iter_rows(min_row=5, values_only=True):
    if r[0]:
        D[(r[0], r[4], r[12], r[13])] = r[17]

groups = [('mlkem512', 'ML-KEM-512', ['keygen', 'encaps', 'decaps']),
          ('mlkem768', 'ML-KEM-768', ['keygen', 'encaps', 'decaps']),
          ('mlkem1024', 'ML-KEM-1024', ['keygen', 'encaps', 'decaps']),
          ('x25519', 'X25519', ['keygen', 'derive']), ('p256', 'P-256', ['keygen', 'derive']),
          ('p384', 'P-384', ['keygen', 'derive']), ('p521', 'P-521', ['keygen', 'derive'])]
short = {'keygen': 'KG', 'encaps': 'Enc', 'decaps': 'Dec', 'derive': 'Der'}
ops = [(a, p) for a, _, ps in groups for p in ps]
PL = 'hybrid'
rc = [D[('hz3', PL, a, p)] / D[('m33-asmoff', PL, a, p)] for a, p in ops]
ra = [D[('hz3', PL, a, p)] / D[('m33-asmon', PL, a, p)] for a, p in ops]

EDGE = '#555555'
plt.rcParams['hatch.linewidth'] = 0.6
series = [(rc, 'Hazard3 / Cortex-M33 C (ISA only: same C code)', 'white', ''),
          (ra, 'Hazard3 / Cortex-M33 asm (ISA + Arm assembly)', 'white', '\\\\\\\\')]

fig, ax = plt.subplots(figsize=(515.52 / 72, 180 / 72))
x = np.arange(len(ops)); w = 0.36
for i, (v, lab, fc, h) in enumerate(series):
    xs = x + (i - 0.5) * w
    ax.bar(xs, v, w, color=fc, edgecolor=EDGE, linewidth=0.7, hatch=h, label=lab, zorder=3)
    for xi, vi in zip(xs, v):
        ax.text(xi, max(vi, 1.0) * 1.03, f'{vi:.2f}' if vi < 2 else f'{vi:.1f}', rotation=90,
                ha='center', va='bottom', fontsize=5.3, color='#333333')
ax.set_ylim(0.8, 12); ax.set_yscale('log'); ax.minorticks_off()
ax.set_yticks([1, 2, 4, 8]); ax.set_yticklabels(['1', '2', '4', '8'], fontsize=8)
ax.axhline(1, color='black', ls='--', lw=0.8, zorder=2)
ax.set_xlim(-0.6, len(ops) - 0.4)
ax.set_ylabel('Hazard3 time / M33 time', fontsize=8.5)
ax.grid(axis='y', color='#e3e3e3', lw=0.6, zorder=0)
for s in ('top', 'right'): ax.spines[s].set_visible(False)
k = 0
for _, _, ps in groups[:-1]:
    k += len(ps); ax.axvline(k - 0.5, color='#bbbbbb', lw=0.7, zorder=1)
ax.set_xticks(x); ax.set_xticklabels([short[p] for _, p in ops], fontsize=7.5)
k = 0
for _, name, ps in groups:
    ax.text(k + (len(ps) - 1) / 2, -0.25, name, transform=ax.get_xaxis_transform(),
            ha='center', va='top', fontsize=7.5)
    k += len(ps)
hd, lb = ax.get_legend_handles_labels()
fig.legend(hd, lb, loc='upper center', ncol=2, fontsize=7.5, frameon=False, bbox_to_anchor=(0.5, 1.0))
fig.tight_layout(rect=(0, 0, 1, 0.9)); fig.subplots_adjust(bottom=0.25)
out = str(OUT / 'fig4_isa')
fig.savefig(out + '.pdf'); fig.savefig(out + '.png', dpi=300)
print(' '.join(f'{v:.2f}' for v in rc)); print(' '.join(f'{v:.2f}' for v in ra))
