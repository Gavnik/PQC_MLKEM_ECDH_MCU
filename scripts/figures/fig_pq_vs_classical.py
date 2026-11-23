"""fig_pq_vs_classical.py -- regenerates fig3_pq_vs_classical.pdf/.png in figures/ from data/summary/layer1_results_v7.xlsx."""
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
rows = [r for r in wb['RQ1_PQ_vs_classical'].iter_rows(min_row=5, values_only=True) if r[0] and r[3]]
R = {(r[0], r[4]): (r[7], r[10]) for r in rows}

pairs = [('x25519', 'ML-KEM-512\nvs X25519'), ('p256', 'ML-KEM-512\nvs P-256'),
         ('p384', 'ML-KEM-768\nvs P-384'), ('p521', 'ML-KEM-1024\nvs P-521')]
cores = [('m33 asm on', 'Cortex-M33 (asm)'), ('hz3 (C)', 'Hazard3 (C)'),
         ('m4 asm on', 'Cortex-M4F (asm)'), ('lx7', 'Xtensa LX7 (fast)')]


def val(img, cl, side):
    if img == 'lx7':
        img = 'lx7 c25519 fast' if cl == 'x25519' else 'lx7 c25519 small'
    return R[(img, cl)][side]


def draw(colors, out, edge, hatches=('', '', '', '')):
    fig, axs = plt.subplots(1, 2, figsize=(515.52 / 72, 194.4 / 72), sharey=False)
    x = np.arange(len(pairs)); w = 0.2
    for side, ax, title in [(0, axs[0], '(a) Client: KeyGen + Decaps vs keygen + derive'),
                            (1, axs[1], '(b) Server: Encaps vs keygen + derive')]:
        for i, (img, lab) in enumerate(cores):
            v = [val(img, cl, side) for cl, _ in pairs]
            xs = x + (i - 1.5) * w
            ax.bar(xs, v, w, color=colors[i], edgecolor=edge, linewidth=0.6, label=lab, zorder=3, hatch=hatches[i])
            for xi, vi in zip(xs, v):
                ax.text(xi, max(vi, 1.0) * 1.08, f'{vi:.1f}', rotation=90, ha='center',
                        va='bottom', fontsize=6.5, color='#333333')
        ax.set_yscale('log'); ax.set_ylim(0.4, 150)
        ax.set_yticks([0.5, 1, 2, 5, 10, 20, 50, 100])
        ax.set_yticklabels(['0.5', '1', '2', '5', '10', '20', '50', '100'], fontsize=8)
        ax.minorticks_off()
        ax.axhline(1, color='black', ls='--', lw=0.8, zorder=2)
        ax.set_xticks(x); ax.set_xticklabels([p for _, p in pairs], fontsize=7.3)
        ax.set_title(title, fontsize=8, loc='left')
        ax.grid(axis='y', color='#e3e3e3', lw=0.6, zorder=0)
        for s in ('top', 'right'): ax.spines[s].set_visible(False)
    axs[0].set_ylabel('ECDH time / ML-KEM time', fontsize=8)
    h, l = axs[0].get_legend_handles_labels()
    fig.legend(h, l, loc='upper center', ncol=4, fontsize=7.5, frameon=False, bbox_to_anchor=(0.5, 1.0))
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    fig.savefig(out + '.pdf'); fig.savefig(out + '.png', dpi=300)


PALE = ['#D0E1F6', '#FBDED2', '#CDEDE2', '#FBEAC7']
plt.rcParams['hatch.linewidth'] = 0.6
draw(PALE, str(OUT / 'fig3_pq_vs_classical'), '#555555', ('', '////', '....', '\\\\\\\\'))
