"""fig_placement.py -- regenerates fig5_placement.pdf/.png in figures/ from data/summary/layer1_results_v7.xlsx."""
import math, openpyxl
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
        D[(r[0], r[4], r[12], r[13])] = (r[17], r[18], r[19])   # mean, sd total, round sd


def wsd(m):
    _, sd, rsd = m
    return math.sqrt((299 * sd ** 2 - 200 * rsd ** 2) / 297)


groups = [('mlkem512', 'ML-KEM-512', ['keygen', 'encaps', 'decaps']),
          ('mlkem768', 'ML-KEM-768', ['keygen', 'encaps', 'decaps']),
          ('mlkem1024', 'ML-KEM-1024', ['keygen', 'encaps', 'decaps']),
          ('x25519', 'X25519', ['keygen', 'derive']), ('p256', 'P-256', ['keygen', 'derive']),
          ('p384', 'P-384', ['keygen', 'derive']), ('p521', 'P-521', ['keygen', 'derive'])]
short = {'keygen': 'KG', 'encaps': 'Enc', 'decaps': 'Dec', 'derive': 'Der'}
cores = [('m33-asmon', 'Cortex-M33 (asm)'), ('hz3', 'Hazard3 (C)'),
         ('m4-asmon', 'Cortex-M4F (asm)'), ('lx7-c25519fast', 'Xtensa LX7 (fast)')]
COL = ['#D0E1F6', '#FBDED2', '#CDEDE2', '#FBEAC7']
HAT = ['', '////', '....', '\\\\\\\\']
EDGE = '#555555'
plt.rcParams['hatch.linewidth'] = 0.6

ops = [(a, p) for a, _, ps in groups for p in ps]
x = np.arange(len(ops)); w = 0.2

mean_r = {c: [D[(c, 'flash', a, p)][0] / D[(c, 'hybrid', a, p)][0] for a, p in ops] for c, _ in cores}
sd_r = {c: [wsd(D[(c, 'flash', a, p)]) / wsd(D[(c, 'hybrid', a, p)]) for a, p in ops] for c, _ in cores}


def fmt_b(v):
    return f'{v:.2f}' if v < 10 else (f'{v:.1f}' if v < 100 else f'{v:.0f}')


fig, axs = plt.subplots(2, 1, figsize=(515.52 / 72, 374.4 / 72))
for ax, data, fmt, title, ylab, ticks, lim in [
    (axs[0], mean_r, lambda v: f'{v:.2f}', '(a) Mean time, flash / hybrid   (above 1 = hybrid faster)',
     'Flash / hybrid time', [0.8, 1, 2, 4, 8], (0.75, 12)),
    (axs[1], sd_r, fmt_b, '(b) Within-round standard deviation, flash / hybrid   (above 1 = hybrid steadier)',
     'Flash / hybrid SD', [1, 2, 5, 10, 20, 50, 100, 200, 500], (0.55, 1100))]:
    for i, (c, lab) in enumerate(cores):
        xs = x + (i - 1.5) * w
        v = data[c]
        ax.bar(xs, v, w, color=COL[i], edgecolor=EDGE, linewidth=0.6, hatch=HAT[i], label=lab, zorder=3)
        for xi, vi in zip(xs, v):
            ax.text(xi, max(vi, 1.0) * 1.04, fmt(vi), rotation=90, ha='center', va='bottom',
                    fontsize=5.3, color='#333333')
    ax.set_yscale('log'); ax.set_ylim(*lim); ax.minorticks_off()
    ax.set_yticks(ticks); ax.set_yticklabels([f'{t:g}' for t in ticks], fontsize=8)
    ax.axhline(1, color='black', ls='--', lw=0.8, zorder=2)
    ax.set_xlim(-0.6, len(ops) - 0.4)
    ax.set_title(title, fontsize=8.5, loc='left')
    ax.set_ylabel(ylab, fontsize=8.5)
    ax.grid(axis='y', color='#e3e3e3', lw=0.6, zorder=0)
    for s in ('top', 'right'): ax.spines[s].set_visible(False)
    # group separators
    k = 0
    for _, _, ps in groups[:-1]:
        k += len(ps); ax.axvline(k - 0.5, color='#bbbbbb', lw=0.7, zorder=1)
    ax.set_xticks(x)
    ax.set_xticklabels([short[p] for _, p in ops] if ax is axs[1] else [], fontsize=7.5)
    if ax is axs[0]: ax.tick_params(axis='x', length=0)
# group names under panel (b)
k = 0
for _, name, ps in groups:
    axs[1].text(k + (len(ps) - 1) / 2, -0.17, name, transform=axs[1].get_xaxis_transform(),
                ha='center', va='top', fontsize=7.5)
    k += len(ps)
h, l = axs[0].get_legend_handles_labels()
fig.legend(h, l, loc='upper center', ncol=4, fontsize=7.5, frameon=False, bbox_to_anchor=(0.5, 1.0))
fig.tight_layout(rect=(0, 0.03, 1, 0.96), h_pad=1.2)
out = str(OUT / 'fig5_placement')
fig.savefig(out + '.pdf'); fig.savefig(out + '.png', dpi=300)

# print values for checking
for c, _ in cores:
    print(c, ' '.join(f'{v:.2f}' for v in mean_r[c]))
    print('  sd', ' '.join(fmt_b(v) for v in sd_r[c]))
