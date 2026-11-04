# Registry, GitHub and the offline index

The skill works with no network at all. The Nextflow Registry and GitHub are used only when
they can be reached. They make discovery fresher, but nothing depends on them.

## Sources

| Source | Provides | Needs network |
|---|---|---|
| Nextflow Registry (`nextflow module search` / `view -output json`) | Search ranking; the nf-core/modules commit each module is published from (`version: 0.0.0-<sha>`); description, tools, inputs and outputs. **No** containers, conda pins, script flags or file list. | Yes |
| GitHub nf-core/modules (raw files; contents, compare and commits APIs) | The component's files at any commit, which of two commits is newer, and a short sha's full form. The APIs are rate-limited to 60 calls/hour without a token. | Yes |
| Offline index `references/nf-core-index.json` | Every nf-core module and subworkflow at the pinned commit (`git_sha`: `7fb83dc4f68838d9650503aff4aec3a3e4072196`); inputs, outputs, containers, conda pins, script flags, file list; and recorded replacements for Seqera-hosted images. | No |
| Snapshot `assets/nf-core-snapshot/` (optional; not every deployment ships it) | When present: files for the most-used components at the pinned commit, so they can be vendored without GitHub. | No |

## What each question uses

| Question | Source |
|---|---|
| Which modules fit this step? (`nfcore_index.py search`) | The Registry first. The offline index when offline mode is on, `nextflow` is missing, the command fails, times out or prints no JSON, or the Registry has no nf-core match for the requested kind. The output's `source` and `reason` say which source answered, and why. |
| Is our pinned copy current? (`nfcore_index.py freshness`) | The Registry's commit, compared by file contents, then by commit order when the contents differ (below). |
| Inputs, flags, containers, conda, file list | The offline index (`show`, `module_flags.py --name`, `find_container.py --package`). For a `newer_available` or `not_in_index` module: the freshness `component`, read from the files at the Registry commit (`--save` writes them to disk for `module_flags.py --main-nf` and `find_container.py --module-dir`). |
| Vendoring (`vendor_nfcore.py`) | GitHub at the chosen commit (default: the pin; `--sha` for the Registry commit), then the snapshot if shipped (pinned commit only), then container-only. The delivered pipeline never uses Registry includes (`from 'nf-core/x'`) or `nextflow module install`. |

## How freshness is decided

1. `nextflow module view nf-core/<name> -output json`, then take the commit from its `version`
   (`0.0.0-81b7daa` → `81b7daa`). `--kind` is optional: when it's omitted, the index decides
   whether the name is a module or a subworkflow. An unindexed name is treated as a module.
2. If the pin starts with that commit, the status is `current`.
3. Otherwise, fetch each file in the index's `files` list at the Registry commit, and compare the
   bytes with the pinned copy. The pinned copy is the snapshot when one is shipped with the component, and
   GitHub at the pin otherwise. If every file is identical, the status is `current`.
4. If any file differs or is missing, or the module isn't in the index, GitHub's compare API
   (`/repos/nf-core/modules/compare/<pin>...<registry commit>`) decides the order:
   - `ahead`: the Registry commit is newer. The status is `newer_available`, or `not_in_index`
     for a module added after the pin.
   - `behind`: the Registry commit is older than the pin. For an indexed module the status is
     `pinned_newer`: keep the pin, because this is not an upgrade. For an unindexed module the
     status is `removed_upstream`: the index holds every component at the pin, so the module
     was removed or renamed before it. Don't vendor it.
   - `identical`, `diverged`, or any failure: the status is `unknown`, with a `reason`.
5. For `newer_available` and `not_in_index`, the short commit is resolved to its full
   40-character sha with the GitHub commits API. nf-core's `modules.json` stores full shas, and
   this is the `registry_sha` that is returned. If the sha can't be resolved, the status is
   `unknown` and the pin is kept. The component is then read at that commit:
   - **File list.** The GitHub contents API lists the folder (`file_list: "listed"`), skipping
     `tests/` and dotfiles as the index does. If the API is rate-limited or down, the list is
     `main.nf`, `meta.yml`, `environment.yml`, the index's own `files`, and every
     `template '<x>'` the `main.nf` uses as `templates/<x>` (`file_list: "inferred"`). Files that
     are absent in inferred mode are skipped.
   - **Fields.** Containers come from `main.nf`, conda pins from `environment.yml`, script flags
     from `main.nf`'s script block, and subworkflow `components` from `meta.yml`. Description,
     tools, inputs and outputs come from the Registry view.
6. Any failure (offline, Registry, or GitHub) gives `unknown` with a `reason`. Use the pinned
   index entry, and flag it at the approval gate.

Only `newer_available` and `not_in_index` lead to vendoring at the Registry commit (`--sha`).
`current`, `pinned_newer` and `unknown` keep the pin; `removed_upstream` isn't vendored at all. Identical files mean `current`,
whatever the commit order. For example, the Registry's commit for salmon/quant (`81b7daa`) is
28 commits *behind* the pin, but its files are identical (checked 2026-09-28). The compare call
is made only when the files differ, so a `current` answer costs no API call.

## Offline mode (enterprise, air-gapped)

Set `NEXTFLOW_SKILLS_OFFLINE=1` (also `true`, `yes` or `on`), or pass `--offline` to
`nfcore_index.py`, `vendor_nfcore.py` and `find_container.py`. Then:

- `search` never runs `nextflow`. It answers from the offline index with
  `"source": "offline", "reason": "offline mode (...)"`.
- `freshness` returns `unknown` without running `nextflow` or calling GitHub, including the
  compare API.
- `vendor_nfcore.py` never calls GitHub. It uses a shipped snapshot if present, otherwise
  container-only (exit 3). With a snapshot, `--sha` falls back to the pinned copy and reports
  `requested_sha`.
- `find_container.py --package` and `--module-dir` are local. `--biocontainers` (quay.io)
  exits 2 without calling quay.io: ask the user for an image instead.
- `compose.py` (pipeline-from-analysis) replaces Seqera-hosted images only from the index's
  recorded `biocontainers` replacements; it skips the quay.io lookup, so any other upstream image
  is retained and reported. Biocontainers images still need quay.io egress at run time; an
  ECR-only deployment needs them mirrored, so list them in the handoff.

Without the switch, a missing network gives the same result, after each call fails or times
out (the Registry timeout is 60 s, GitHub's is 20 s). The switch just skips the waiting.

Offline, the index still answers discovery and planning. You lose:

- modules added or changed after the pin;
- the Registry's search ranking;
- vendoring of nf-core code (except components in a shipped snapshot). Those become container-only: a local module
  that reuses the pinned nf-core container URI. The HPC run must still be able to pull that
  image.

## Refreshing the shipped index (maintainers)

Maintainers regenerate the index (and the snapshot, where one is shipped) from a chosen
nf-core/modules commit, including any approved container replacements and each component's
`optional_input_hazards`. The current index is pinned to
`7fb83dc4f68838d9650503aff4aec3a3e4072196`; its metadata has no separate date field. When the pin changes, maintainers
update the recorded pin and repository checks that assert the shipped metadata, then run the
package test suite. Components without a shipped snapshot copy are vendored from GitHub when online; offline they use the container-only fallback and must be flagged at
the approval gate.
