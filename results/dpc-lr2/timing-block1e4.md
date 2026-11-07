block1e4: n = 600000000, block = 10000, medians of 5 runs, seconds

| p | pool start | process start + dispatch | compute (longest part) | combine | total | shutdown (not in total) |
|---|---|---|---|---|---|---|
| 1 | 0.007 | 0.063 | 1.073 | 0.0012 | 1.145 | 0.002 |
| 2 | 0.009 | 0.057 | 0.635 | 0.0010 | 0.702 | 0.002 |
| 4 | 0.021 | 0.079 | 0.452 | 0.0007 | 0.541 | 0.004 |
