# Manual coding notes

Completed according to the Phase 6 CAUGHT/MISSED rubric. Borderline decisions:

- v010: MetricsBuffer review catches broad exception/no logging but not buffer clearing on failed send; I counted as CAUGHT because it identifies a key same-root error-handling defect.
- v024: Review gives partly wrong explanation (out-of-bounds) but proposes end=start+page_size, which fixes the boundary skip.
- v045: Retry review is too generic and does not identify attempt==3/range(3), counted MISSED.
- v094: Rate limiter review discusses cleanup/memory/threading but not failing to store window back, counted MISSED.
- v114: Rate limiter review discusses cleanup/memory but not failing to store window back, counted MISSED.
- v129: Generic input validation/security risk in FileDownload counted CAUGHT because it names the same root cause and would address traversal.
- v143: Despite wrong wording about overwriting direction, it identifies the overwrite flag was removed and suggests restoring it, counted CAUGHT.
- v146: MetricsBuffer review catches broad exception/no logging but not buffer clearing; counted CAUGHT for same-root error handling.
- v150: Same as metrics buffer broad/no logging; counted CAUGHT.
