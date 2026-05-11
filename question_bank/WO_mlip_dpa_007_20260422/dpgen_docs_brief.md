# DP-GEN Quick Notes

- Environment: Python 3.11+, conda/uv, MPI runtime.
- Installation: install dpgen and deepmd-kit, verify CLI is callable.
- Initial data: prepare seed structures and reference labels.
- Iteration loop: train -> explore/sample -> label -> update dataset -> retrain.
- Common failures: bad environment path, missing labels, unstable model.
- Suggested first week:
  - Day 1: environment + install check
  - Day 2: initial dataset preparation
  - Day 3: baseline training
  - Day 4: exploration sampling
  - Day 5: labeling pipeline
  - Day 6: retraining and validation
  - Day 7: summary and next-iteration plan
