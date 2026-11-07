base: n = 600000000, block = 1000000, medians of 5 runs, seconds

| p | pool start | process start + dispatch | compute (longest part) | combine | total | shutdown (not in total) |
|---|---|---|---|---|---|---|
| 1 | 0.007 | 0.061 | 3.710 | 0.0008 | 3.776 | 0.002 |
| 2 | 0.008 | 0.060 | 2.592 | 0.0007 | 2.707 | 0.002 |
| 4 | 0.030 | 0.072 | 2.032 | 0.0005 | 2.150 | 0.005 |
