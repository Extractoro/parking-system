pc12-before: n = 600000000, block = 1000000, medians of 5 runs, seconds

| p | pool start | process start + dispatch | compute (longest part) | combine | total | shutdown (not in total) |
|---|---|---|---|---|---|---|
| 1 | 0.007 | 0.122 | 8.332 | 0.0016 | 8.400 | 0.006 |
| 2 | 0.017 | 0.195 | 5.531 | 0.0014 | 5.716 | 0.005 |
| 4 | 0.050 | 0.390 | 4.714 | 0.0017 | 5.189 | 0.008 |
