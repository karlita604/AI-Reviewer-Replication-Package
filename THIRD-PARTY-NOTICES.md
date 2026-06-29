# Third-Party Notices

Some patches in this dataset are derived from functions in third-party
open-source projects. These are the patches whose `metadata.json` contains
`"source": "injected"`; each records its origin repository, commit, and file in
its metadata. The derived portions remain under their original licenses:

| Upstream project | License        |
|------------------|----------------|
| psf/requests     | Apache-2.0     |
| pallets/flask    | BSD-3-Clause   |
| pallets/click    | BSD-3-Clause   |
| tiangolo/fastapi | MIT            |

For each injected patch, the original function was modified to introduce a
single documented defect (in the known-bad set) or a verified-correct change (in
the verified-good set); the modification is recorded per patch in the metadata.
These permissive licenses allow redistribution of modified code provided the
original copyright and license notices are preserved and modifications are
indicated, which this notice and the per-patch provenance metadata together do.
We thank the maintainers of these projects.
