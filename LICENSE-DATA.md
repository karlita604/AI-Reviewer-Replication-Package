# Data License

The dataset and processed results in this package that were created by the
authors are licensed under the **Creative Commons Attribution 4.0 International
License (CC-BY-4.0)**: https://creativecommons.org/licenses/by/4.0/

This covers:

- the manually written known-bad patches and the manually written verified-good
  patches,
- the four description conditions for every patch,
- the bug metadata and audit files,
- the processed result tables under `results/` (parsed verdicts, detection
  labels, signal-detection tables, probe scores, and analysis outputs).

**Exception — injected patches.** The patches whose `metadata.json` has
`"source": "injected"` are derivative works of third-party open-source projects
and retain those projects' original licenses, not CC-BY-4.0. Each such patch
records its origin repository, commit, and file in its metadata. See
`THIRD-PARTY-NOTICES.md`.

Copyright/author information is withheld for double-anonymous review and will be
added upon de-anonymization.
