# Combined Experiment Summary

# IIC Summary

Decision rule: reject H0 when the lower bound of the 95% confidence interval is greater than 0.5.

## vs random
| ontology   | comparison   |     mean |         sd |   n |   ci_low |   ci_high | reject_h0   |
|:-----------|:-------------|---------:|-----------:|----:|---------:|----------:|:------------|
| bctt       | vs random    | 0.978492 | 0.0189527  | 100 | 0.974731 |  0.982253 | True        |
| co-wheat   | vs random    | 0.994783 | 0.0499794  | 100 | 0.984866 |  1.0047   | True        |
| elig       | vs random    | 0.746782 | 0.248383   | 100 | 0.697497 |  0.796067 | True        |
| hom        | vs random    | 0.968357 | 0.0750905  | 100 | 0.953458 |  0.983257 | True        |
| ogr        | vs random    | 0.966291 | 0.118485   | 100 | 0.942781 |  0.989801 | True        |
| pe         | vs random    | 0.982886 | 0.0853701  | 100 | 0.965946 |  0.999825 | True        |
| taxrank    | vs random    | 0.996763 | 0.00388063 | 100 | 0.995993 |  0.997533 | True        |
| xeo        | vs random    | 0.970052 | 0.0837159  | 100 | 0.953441 |  0.986663 | True        |
| overall    | vs random    | 0.950551 | 0.135149   | 800 | 0.941171 |  0.95993  | True        |

## vs non_in_largest_mcs
| ontology   | comparison            |     mean |        sd |   n |   ci_low |   ci_high | reject_h0   |
|:-----------|:----------------------|---------:|----------:|----:|---------:|----------:|:------------|
| bctt       | vs non_in_largest_mcs | 0.959088 | 0.0296108 | 100 | 0.953213 |  0.964964 | True        |
| co-wheat   | vs non_in_largest_mcs | 0.692797 | 0.242334  | 100 | 0.644712 |  0.740881 | True        |
| elig       | vs non_in_largest_mcs | 0.617357 | 0.251193  | 100 | 0.567515 |  0.667199 | True        |
| hom        | vs non_in_largest_mcs | 0.820476 | 0.209382  | 100 | 0.77893  |  0.862022 | True        |
| ogr        | vs non_in_largest_mcs | 0.690339 | 0.239245  | 100 | 0.642867 |  0.73781  | True        |
| pe         | vs non_in_largest_mcs | 0.5      | 0         | 100 | 0.5      |  0.5      | False       |
| taxrank    | vs non_in_largest_mcs | 0.895291 | 0.198691  | 100 | 0.855866 |  0.934715 | True        |
| xeo        | vs non_in_largest_mcs | 0.931125 | 0.144635  | 100 | 0.902426 |  0.959824 | True        |
| overall    | vs non_in_largest_mcs | 0.763309 | 0.242392  | 800 | 0.746487 |  0.780131 | True        |

## vs weakening
| ontology   | comparison   |     mean |       sd |   n |   ci_low |   ci_high | reject_h0   |
|:-----------|:-------------|---------:|---------:|----:|---------:|----------:|:------------|
| bctt       | vs weakening | 0.895664 | 0.114856 | 100 | 0.872874 |  0.918454 | True        |
| co-wheat   | vs weakening | 0.727456 | 0.247684 | 100 | 0.67831  |  0.776602 | True        |
| elig       | vs weakening | 0.533753 | 0.271219 | 100 | 0.479937 |  0.587569 | False       |
| hom        | vs weakening | 0.853917 | 0.209103 | 100 | 0.812427 |  0.895408 | True        |
| ogr        | vs weakening | 0.757126 | 0.243375 | 100 | 0.708835 |  0.805417 | True        |
| pe         | vs weakening | 0.573782 | 0.176525 | 100 | 0.538756 |  0.608808 | True        |
| taxrank    | vs weakening | 0.869472 | 0.247262 | 100 | 0.820409 |  0.918534 | True        |
| xeo        | vs weakening | 0.911563 | 0.194887 | 100 | 0.872893 |  0.950232 | True        |
| overall    | vs weakening | 0.765342 | 0.256677 | 800 | 0.747528 |  0.783155 | True        |

# Runtime Summary

Average runtime of successful trials (mean, in milliseconds).

| ontology | Random removal | Not-in-largest-MCS removal | Weakening | Power index |
| --- | --- | --- | --- | --- |
| bctt | 56.11 ms | 5149.51 ms | 736.69 ms | 2834.72 ms |
| co-wheat | 144.41 ms | 6635.80 ms | 1259.83 ms | 15546.08 ms |
| elig | 28.49 ms | 713.50 ms | 457.39 ms | 10457.51 ms |
| hom | 34.36 ms | 1223.60 ms | 442.62 ms | 2391.02 ms |
| ogr | 32.16 ms | 181.27 ms | 288.44 ms | 1656.16 ms |
| pe | 71.48 ms | 6109.09 ms | 809.75 ms | 6624.72 ms |
| taxrank | 48.70 ms | 1737.46 ms | 416.18 ms | 8763.94 ms |
| xeo | 50.22 ms | 15125.16 ms | 1544.61 ms | 57622.50 ms |
| overall | 58.24 ms | 4609.42 ms | 744.44 ms | 13237.08 ms |
# Power-index Outcome Rates

Outcome rates for the full trial (all four repairs are run in sequence).

| ontology | success | time_limit_exceeded | memory_limit_exceeded |
| --- | --- | --- | --- |
| bctt | 100 (100.0%) | 0 (0.0%) | 0 (0.0%) |
| co-wheat | 100 (100.0%) | 0 (0.0%) | 0 (0.0%) |
| elig | 100 (100.0%) | 0 (0.0%) | 0 (0.0%) |
| hom | 100 (100.0%) | 0 (0.0%) | 0 (0.0%) |
| ogr | 100 (100.0%) | 0 (0.0%) | 0 (0.0%) |
| pe | 100 (100.0%) | 0 (0.0%) | 0 (0.0%) |
| taxrank | 100 (100.0%) | 0 (0.0%) | 0 (0.0%) |
| xeo | 100 (89.3%) | 10 (8.9%) | 2 (1.8%) |
| overall | 800 (98.5%) | 10 (1.2%) | 2 (0.2%) |
