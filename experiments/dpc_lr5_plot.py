"""DPC lab 5: delay of every finalized window and dropped events per window, watermark 2 and 5 min (O5.3).

    uv run --no-project --python 3.14 --with matplotlib python experiments/dpc_lr5_plot.py
Reads results/dpc-lr5/lateness.json, writes docs/reports/img/dpc-lr5-lateness.png.
"""

import json
from collections import Counter
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

data = json.loads(Path("results/dpc-lr5/lateness.json").read_text(encoding="utf-8"))
fig, (left, right) = plt.subplots(1, 2, figsize=(11, 3.8))
for (run, x), color in zip(data.items(), ("#d1495b", "#00798c"), strict=True):
    wm = x["watermark_minutes"]
    drops = Counter(e["event_time"][11:15] + ("0" if e["event_time"][15] < "5" else "5") for e in x["dropped_events"])
    labels = sorted({f"{h:02d}:{m:02d}" for h in (7,) for m in range(0, 60, 5)})
    right.plot(range(len(labels)), [drops.get(t, 0) for t in labels], marker="o", color=color, label=f"watermark {wm} хв")
    right.set_xticks(range(len(labels)), labels, rotation=45, fontsize=8)
left.bar(["2 хв", "5 хв"], [x["delay_s"]["median"] for x in data.values()], color=["#d1495b", "#00798c"])
for i, x in enumerate(data.values()):
    left.errorbar(i, x["delay_s"]["median"], yerr=[[x["delay_s"]["median"] - x["delay_s"]["min"]],
                  [x["delay_s"]["max"] - x["delay_s"]["median"]]], color="black", capsize=6)
left.set_title("Затримка вікна, с (медіана, мін-макс)", fontsize=10)
left.set_xlabel("допустиме запізнення")
right.set_title("Відкинуті події за початком вікна (усі паркінги)", fontsize=10)
right.legend(fontsize=9)
fig.tight_layout()
fig.savefig("docs/reports/img/dpc-lr5-lateness.png", dpi=150)
print("docs/reports/img/dpc-lr5-lateness.png")
