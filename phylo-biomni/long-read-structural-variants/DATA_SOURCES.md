# Data and software sources

| Name | Type | Version | URI | License | Commercial use | Included | Verification |
|---|---|---:|---|---|---|---:|---|
| Sniffles2 | software | 2.8.0 | https://github.com/fritzsedlazeck/Sniffles | MIT | allowed | true | `references/sv_calling.md` |
| cuteSV | software | installed version recorded at runtime | https://github.com/tjiangHIT/cuteSV | GPL-3.0 | allowed when invoked as an installed tool | true | `references/sv_calling.md` |
| pysam with htslib/bcftools/samtools | software | 0.24.0 | https://github.com/pysam-developers/pysam | MIT | allowed | true | `scripts/license_audit.py` |
| numpy | software | 2.4.6 | https://github.com/numpy/numpy | BSD-3-Clause | allowed | true | `scripts/license_audit.py` |
| matplotlib | software | 3.10.5 | https://github.com/matplotlib/matplotlib | Matplotlib/PSF-compatible | allowed | true | `scripts/license_audit.py` |
| reportlab | software | 4.4.3 | https://www.reportlab.com/ | BSD | allowed | true | `scripts/license_audit.py` |

The package contains no sample or validation dataset. Runtime scientific inputs must be explicit uploaded/downloaded selections recorded in schema-v2 `input_selection.json`. Installed versions and license evidence are recorded in `sv/dependency_licenses.json`; missing or conflicting evidence stops analysis.
