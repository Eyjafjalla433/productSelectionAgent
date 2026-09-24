# Ranking test results

All results below use already-materialized complete requirements. Ground-truth
IDs are joined only after ranking to calculate metrics.

## Frozen public development set (200 sessions)

| Stage / cutoff | Hits | Rate |
|---|---:|---:|
| Top50 candidate coverage | 199/200 | 99.50% |
| Locked weighted-RRF Top1 | 173/200 | 86.50% |
| Locked weighted-RRF Top3 | 193/200 | 96.50% |
| Locked weighted-RRF Top5 | 197/200 | 98.50% |
| Locked weighted-RRF Top10 | 198/200 | 99.00% |

MRR@10 is `0.912625`. The locked schedule was selected on the proxy data and
then reused on the public set. A public-label-selected `199/200` schedule is
not reported as the locked result.

The three-turn diagnostic that projects the public set to 100 buying and 100
browsing sessions also returns 198/200, MRR `0.913042`, and TechnicalScore
`0.927313`. That run validates the adapter wiring; it is not the original
four-scenario official score.

## Synthetic proxy set (3,021 sessions)

| Stage / cutoff | Hits | Rate |
|---|---:|---:|
| Top50 candidate coverage | 3,001/3,021 | 99.34% |
| Locked weighted-RRF Top1 | 2,586/3,021 | 85.60% |
| Locked weighted-RRF Top3 | 2,841/3,021 | 94.04% |
| Locked weighted-RRF Top5 | 2,899/3,021 | 95.96% |
| Locked weighted-RRF Top10 | 2,960/3,021 | 97.98% |

Conditional on the target being present in Top50, Top10 recall is
`2,960/3,001 = 98.63%`; MRR@10 is `0.901193`.

## Why the full-fit learned ranker is not the default

An exploratory pairwise ranker fitted and evaluated on all covered proxy
sessions placed all 3,001 covered targets in Top5. This is a same-data full-fit
upper bound, not a generalization estimate. On the frozen public set it fell to
193/200 Top10, compared with 198/200 for locked weighted-RRF. The package
therefore defaults to the conservative locked ranker.

Machine-readable summaries are under [`docs/results/`](results/). The 3,021
sessions are synthetic positive-unlabeled proxies, not organizer private-800
sessions and not proof of the private distribution.
