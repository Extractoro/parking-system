pc12: n = 600000000, block = 1000000, medians of 5 runs, seconds

| p | pool start | process start + dispatch | compute (longest part) | combine | total | shutdown (not in total) |
|---|---|---|---|---|---|---|
| 1 | 0.005 | 0.092 | 4.578 | 0.0015 | 4.692 | 0.003 |
| 2 | 0.012 | 0.116 | 3.536 | 0.0028 | 3.682 | 0.003 |
| 4 | 0.025 | 0.096 | 2.621 | 0.0008 | 2.719 | 0.006 |
