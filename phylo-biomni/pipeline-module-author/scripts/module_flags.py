#!/usr/bin/env python3
"""Flags a Nextflow module already sets, and flag splitting for session commands.

  module_flags.py --name salmon/quant --command "salmon quant -i idx -p 8 ..."   # planning: offline index
  module_flags.py --main-nf PATH --command "fastp -i a.fq --thread 8 ..."        # a vendored or local module

Prints JSON: per pipe segment, which flags the module already sets (drop them;
set_by_module_as names the module's spelling when the session used an alias)
and which remain (structural input, fixed ext.args, or a new params.*).
`unparsed` lists every positional token not attached to a flag. Flag arity and
aliases come from a small built-in table for common tools; for other tools a
boolean flag may take the next token as its value, so check `tool --help`.
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from pathlib import Path

FLAG_RE = re.compile(r"(?<![\w$.{-])(--[A-Za-z][A-Za-z0-9_-]*|-[A-Za-z0-9@](?![\w-]))")
_STRING_RE = re.compile(r'"""(.*?)"""|"((?:\\.|[^"\\])*)"|\'((?:\\.|[^\'\\])*)\'', re.S)
_COMMENT_RE = re.compile(r'("""(?:.*?)"""|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')|/\*.*?\*/|//[^\n]*', re.S)
_CONTAINS_RE = re.compile(r"\.contains\(\s*(?:'[^']*'|\"[^\"]*\")\s*\)")
_NEG_NUMBER_RE = re.compile(r"^-\d+(\.\d+)?$")
_SUBCOMMAND_RE = re.compile(r"^[A-Za-z][\w-]*$")

# Short/long spellings of the same option, per program (lower-case program name).
ALIASES = {
    "salmon": [("-i", "--index"), ("-p", "--threads"), ("-l", "--libType"), ("-o", "--output"),
               ("-g", "--geneMap"), ("-t", "--targets"), ("-a", "--alignments"), ("-r", "--unmatedReads"),
               ("-1", "--mates1"), ("-2", "--mates2"), ("-k", "--kmerLen")],
    "fastp": [("-i", "--in1"), ("-I", "--in2"), ("-o", "--out1"), ("-O", "--out2"), ("-w", "--thread"),
              ("-j", "--json"), ("-h", "--html"), ("-l", "--length_required"), ("-q", "--qualified_quality_phred"),
              ("-a", "--adapter_sequence"), ("-m", "--merge")],
    "samtools": [("-@", "--threads"), ("-o", "--output"), ("-O", "--output-fmt")],
    "fastqc": [("-o", "--outdir"), ("-t", "--threads")],
    "trim_galore": [("-j", "--cores"), ("-o", "--output_dir"), ("-q", "--quality")],
    "hisat2": [("-p", "--threads")],
    "bowtie2": [("-p", "--threads")],
    "multiqc": [("-o", "--outdir"), ("-f", "--force"), ("-n", "--filename")],
}
# Flags that never take a value.
BOOLEAN = {
    "salmon": {"--validateMappings", "--gcBias", "--seqBias", "--posBias", "--softclip", "--softclipOverhangs",
               "--writeUnmappedNames", "--useVBOpt", "--noLengthCorrection", "--keepDuplicates", "--dumpEq",
               "--recoverOrphans", "--allowDovetail", "--gencode"},
    "samtools": {"-n", "-u", "-b", "-h", "-H", "-c", "-B"},
    "fastp": {"--detect_adapter_for_pe", "--dedup", "-c", "--correction", "-g", "--trim_poly_g", "-x",
              "--trim_poly_x", "-p", "--overrepresentation_analysis", "-m", "--merge", "-A",
              "--disable_adapter_trimming", "-Q", "--disable_quality_filtering", "-L", "--disable_length_filtering",
              "--interleaved_in", "-6", "--phred64", "-5", "--cut_front", "-3", "--cut_tail", "-r", "--cut_right",
              "-y", "--low_complexity_filter", "--stdout", "--stdin", "--dont_eval_duplication"},
    "bwa": {"-M", "-a", "-p", "-Y", "-C"},
    "fastqc": {"--extract", "--noextract", "-q", "--quiet", "--nogroup", "--casava", "--nano"},
    "trim_galore": {"--paired", "--gzip", "--dont_gzip", "--illumina", "--nextera", "--small_rna", "--fastqc",
                    "--trim-n", "--retain_unpaired", "--rrbs", "--non_directional", "--no_report_file", "--polyA"},
    "featurecounts": {"-p", "-B", "-C", "-M", "-O", "-f", "-L", "--countReadPairs", "--primary", "--fraction",
                      "--ignoreDup"},
    "hisat2": {"--dta", "--no-spliced-alignment", "--no-unal", "--no-mixed", "--no-discordant", "-q", "-f"},
    "bowtie2": {"--very-sensitive", "--sensitive", "--fast", "--very-fast", "--local", "--end-to-end",
                "--very-sensitive-local", "--no-unal", "--no-mixed", "--no-discordant", "-q", "-f"},
    "multiqc": {"-f", "--force", "-z", "--zip-data-dir", "--interactive", "--flat", "--export"},
}
# Programs whose every option takes all following values, and flags that take several values.
MULTI_VALUE_PROGRAMS = {"star"}
MULTI_VALUE = {"salmon": {"-1", "-2", "-r", "--mates1", "--mates2", "--unmatedReads"}}


def aliases(program: str, flag: str) -> set[str]:
    """Every spelling of `flag` for `program`, including itself."""
    names = {flag}
    for short, long in ALIASES.get(program.lower(), []):
        if flag in (short, long):
            names |= {short, long}
    return names


def _script_section(main_nf: str) -> str:
    start = re.search(r"^\s*(?:script|shell):\s*$", main_nf, re.M)
    if not start:
        return ""
    rest = main_nf[start.end():]
    stub = re.search(r"^\s*stub:\s*$", rest, re.M)
    return rest[: stub.start()] if stub else rest


def script_flags(main_nf: str) -> set[str]:
    section = _COMMENT_RE.sub(lambda m: m.group(1) or "", _script_section(main_nf))
    section = _CONTAINS_RE.sub("", section)
    flags: set[str] = set()
    for m in _STRING_RE.finditer(section):
        literal = next(g for g in m.groups() if g is not None) if any(m.groups()) else ""
        flags |= set(FLAG_RE.findall(literal))
    return flags


def _split_segments(command: str) -> list[list[str]]:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = command.split()
    segments, current = [], []
    for tok in tokens:
        if tok in {"|", "&&", ";"}:
            if current:
                segments.append(current)
            current = []
        else:
            current.append(tok)
    if current:
        segments.append(current)
    return segments


def _parse_segment(seg: list[str]) -> dict:
    program = Path(seg[0]).name
    key = program.lower()
    booleans, multi = BOOLEAN.get(key, set()), MULTI_VALUE.get(key, set())
    rest, flags, unparsed, subcommand, i = seg[1:], [], [], [], 0
    if rest and _SUBCOMMAND_RE.match(rest[0]):
        subcommand.append(rest[0])
        i = 1
    while i < len(rest):
        tok = rest[i]
        if not tok.startswith("-") or tok == "-":
            unparsed.append(tok)
            i += 1
            continue
        if tok.startswith("--") and "=" in tok:
            flags.append(tok.split("=", 1))
            i += 1
            continue
        if tok in booleans:
            flags.append([tok, None])
            i += 1
            continue
        values, j = [], i + 1
        takes_many = key in MULTI_VALUE_PROGRAMS or tok in multi
        # A value never starts with "-", except a negative number right after a flag.
        while j < len(rest) and (not rest[j].startswith("-") or rest[j] == "-"
                                 or (not values and _NEG_NUMBER_RE.match(rest[j]))):
            values.append(rest[j])
            j += 1
            if not takes_many:
                break
        flags.append([tok, " ".join(values) if values else None])
        i = j
    return {"program": program, "subcommand": subcommand, "flags": flags, "unparsed": unparsed}


def command_flags(command: str) -> list[dict]:
    return [_parse_segment(seg) for seg in _split_segments(command)]


def classify_with_flags(command: str, already: set[str]) -> list[dict]:
    result = []
    for seg in command_flags(command):
        set_as, done, left = {}, [], []
        for flag in seg["flags"]:
            match = sorted(aliases(seg["program"], flag[0]) & already)
            if match:
                set_as[flag[0]] = flag[0] if flag[0] in match else match[0]
                done.append(flag)
            else:
                left.append(flag)
        result.append({"program": seg["program"], "subcommand": seg["subcommand"], "already_set": done,
                       "set_by_module_as": set_as, "remaining": left, "unparsed": seg["unparsed"]})
    return result


def classify(command: str, main_nf: str) -> list[dict]:
    return classify_with_flags(command, script_flags(main_nf))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--main-nf", type=Path, help="module main.nf in a repo or snapshot")
    source.add_argument("--name", help="nf-core component name in the offline index, e.g. salmon/quant")
    parser.add_argument("--index", type=Path, help="offline index (default: the one shipped with this skill)")
    parser.add_argument("--command", required=True)
    args = parser.parse_args()
    if args.main_nf:
        already = script_flags(args.main_nf.read_text())
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from nfcore_index import DEFAULT_INDEX, load_index, show
        component = show(load_index(args.index or DEFAULT_INDEX), args.name, "module")
        if component is None:
            sys.exit(f"module {args.name!r} is not in the offline index; search it with nfcore_index.py")
        already = set(component.get("script_flags") or [])
    print(json.dumps(classify_with_flags(args.command, already), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
