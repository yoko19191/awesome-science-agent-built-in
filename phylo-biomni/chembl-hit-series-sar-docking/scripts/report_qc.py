#!/usr/bin/env python3
"""Report QC gates for a generated Biomni skill. Copy into the skill's scripts/ and CALL them.

A gate is wired only when a script calls it. Several shipped skills define validators that nothing
invokes — those are documentation, not gates. Call these from your export step.

    from report_qc import assert_generated_by_tool, assert_report_branded, staged_copy

Every deliverable path taken by this module is resolved against RESULTS when relative: the working
directory is /workspace but deliverables must land on the results mount, so a bare "report_facts.json"
written relative to the CWD is invisible to the report builder and to the user.

Stdlib only except assert_figure_ok, which needs Pillow or numpy (it degrades to a size check).
"""

from __future__ import annotations

import base64
import binascii
import csv
import json
import os
import pathlib
import re
import shutil
import subprocess
import zlib

RESULTS = pathlib.Path(os.environ.get("BIOMNI_RESULTS", "/mnt/results"))
_TRANSCRIPT_REL = pathlib.Path("execution_trace") / "transcript.jsonl"   # resolved at call time
TRANSCRIPT = RESULTS / _TRANSCRIPT_REL


class GateFailure(AssertionError):
    """Raised before any facts artifact is written, so a failing run produces no quotable numbers."""


def _at_results(path: str | pathlib.Path) -> pathlib.Path:
    """A relative path names the results mount, not the CWD. Absolute paths are honoured as given.

    Reads RESULTS at call time so a caller (or a test) can repoint the mount.
    """
    p = pathlib.Path(path)
    return p if p.is_absolute() else RESULTS / p


# --- the infographic honesty gate ----------------------------------------------------------------

def _record_text(line: str) -> str:
    """One transcript record flattened to searchable text — never more than one record.

    Transcript shape is not guaranteed, so a line that is not JSON is searched raw.
    """
    try:
        return json.dumps(json.loads(line), ensure_ascii=False)
    except ValueError:
        return line


def assert_generated_by_tool(*filenames: str, transcript: pathlib.Path | None = None) -> None:
    """Fail unless each schematic was really produced by the Biomni GenerateImage tool.

    Why this exists: a box-and-arrow diagram drawn with matplotlib looks like an infographic and
    passes a visual check. The only in-run evidence that the tool actually ran is its own success
    line in the session transcript, and it must be a line naming *that* file — a success elsewhere
    in the transcript is evidence for the file it names and nothing else. Note that GenerateImage
    strips directory components and always writes to the results root, so compare basenames only.

    A relative `transcript` is resolved against RESULTS.
    """
    path = _at_results(transcript if transcript is not None else _TRANSCRIPT_REL)
    if not path.exists():
        raise GateFailure(
            f"cannot verify infographic provenance: {path} is missing. "
            "Do not claim a GenerateImage infographic you cannot evidence."
        )

    wanted = [os.path.basename(fn) for fn in filenames]
    evidenced: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        # The tool returns: "Image generated successfully and saved to /mnt/results/<name>". Match
        # per record: searching the whole file lets one success vouch for every failure, and the
        # requested name is in the transcript merely by having been asked for.
        record = _record_text(line)
        if "generated successfully" not in record:
            continue
        evidenced.update(b for b in wanted if b in record)

    missing = [b for b in wanted if b not in evidenced]
    if missing:
        raise GateFailure(
            "no GenerateImage success line in the transcript for: "
            + ", ".join(missing)
            + ". These were not produced by the tool. Do not present a plotted schematic as an "
              "infographic — generate it with GenerateImage (load it via ToolSearch first)."
        )


def assert_infographic_landscape(*filenames: str, min_ratio: float = 1.2) -> dict:
    """Fail unless each schematic came back LANDSCAPE — wider than tall by at least `min_ratio`.

    Why this exists: the third sibling of assert_generated_by_tool and assert_report_branded. Those
    ask "was this made by the tool" and "did the document go through the delegate". Neither asks what
    SHAPE came back, and shape is what actually broke: a portrait render dropped into the page-1
    banner slot fills the frame as a tall narrow column with dead margins either side, and it passes
    every other gate in this module green. GenerateImage defaults to 3:2; a prompt that overrides it
    to 2:3 is the failure mode, and nothing short of a dimension read catches it.

    What this is NOT: a layout check. It reads the SOURCE raster's dimensions, not the width the image
    was placed at in the PDF — a landscape source scaled into a narrow column still passes here.

    Dimensions come from the PNG IHDR header (bytes 16..24, big-endian uint32) rather than decoding
    the image, so this stays stdlib and costs nothing on a 1.3 MB render.

    Returns {basename: (width, height)} so a caller can record the numbers in its facts artifact.
    """
    sizes: dict = {}
    bad: list = []
    for name in filenames:
        p = _at_results(name)
        if not p.exists():
            raise GateFailure(f"infographic not written: {p}")
        head = p.open("rb").read(24)
        if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
            raise GateFailure(
                f"{p.name} is not a PNG, so its geometry cannot be read. GenerateImage returns PNG; "
                f"a converted or re-encoded file has lost the provenance the other gates rely on."
            )
        w = int.from_bytes(head[16:20], "big")
        h = int.from_bytes(head[20:24], "big")
        if h == 0:
            raise GateFailure(f"{p.name} reports zero height — the file is corrupt")
        sizes[p.name] = (w, h)
        if w / h < min_ratio:
            bad.append(f"{p.name} {w}x{h} (ratio {w / h:.3f} < {min_ratio})")
    if bad:
        raise GateFailure(
            "infographic is not landscape: " + "; ".join(bad)
            + ". Regenerate with aspect_ratio=\"3:2\" (1536x1024) and a horizontal left-to-right "
              "layout. A portrait banner is a defect, not a style choice."
        )
    return sizes

# --- figure sanity -------------------------------------------------------------------------------

def assert_report_exists(report_name: str, min_bytes: int = 20_000) -> pathlib.Path:
    """Fail unless the report was actually produced, at the results root, under its declared name.

    Why this exists: the platform's own guidance tells an agent not to create file deliverables for
    simple queries or single analyses. A skill that declares a mandatory report is therefore asking
    for something the agent may reasonably decide to skip — and without this assertion the run then
    finishes *successfully* with no deliverable and nobody notices.

    This does not stop an agent skipping the step. It stops the run quietly succeeding when it did.
    Call it as the last thing the skill does.
    """
    base = os.path.basename(report_name)
    path = RESULTS / base
    if not path.exists():
        raise GateFailure(
            f"the run is not complete: {base} was not produced at {RESULTS}/. "
            "The report is a required deliverable of this skill, not an optional extra. "
            "Generate it before finishing, or report plainly that the run failed and why."
        )
    size = path.stat().st_size
    if size < min_bytes:
        raise GateFailure(
            f"{base} is only {size} B — a report of a multi-step analysis that small is a "
            "rendering failure, not a concise summary. Check the build for a silent error."
        )
    return path


def assert_figures(manifest: str | pathlib.Path | list[dict]) -> list[dict]:
    """Every declared figure must exist and be non-blank. Returns the manifest for the facts file.

    Call this BEFORE writing report_facts.json, so a run that produced a blank figure stops instead
    of shipping a report that points at it. The returned list is what the report should read its
    figure inventory from — a report that lists figures by reading this cannot claim one it never
    produced, or quietly drop one it did.

    A manifest entry is {"step": 2, "file": "figures/figure_2_qc.png", "caption": "..."}. The
    manifest path and every relative entry `file` are resolved against RESULTS.
    """
    if isinstance(manifest, (str, pathlib.Path)):
        p = _at_results(manifest)
        if not p.exists():
            raise GateFailure(
                f"figure manifest {p} is missing. Declare one entry per analysis step; if a step "
                "genuinely has nothing to plot, record it with \"file\": null and a reason."
            )
        entries = json.loads(p.read_text(encoding="utf-8"))
    else:
        entries = manifest

    if not isinstance(entries, list) or not entries:
        raise GateFailure("figure manifest is empty — an analysis with no figure shows the reader nothing")

    checked = []
    for e in entries:
        fn = e.get("file")
        if fn is None:                       # an explicit, reasoned absence is allowed
            if not e.get("reason"):
                raise GateFailure(f"step {e.get('step')!r} declares no figure and gives no reason")
            checked.append(e)
            continue
        assert_figure_ok(fn)
        if not str(e.get("caption", "")).strip():
            raise GateFailure(
                f"{fn} has no caption. State what the figure shows, not what it is called."
            )
        checked.append(e)
    return checked


def report_embeds_figures(pdf_path: str | pathlib.Path, figures: list[dict]) -> tuple[bool, str]:
    """Soft check: does the built report actually contain the declared figures?

    Returns (ok, detail) rather than raising — this is the deliberately soft half of the figure rule.
    Degrades to 'not evaluable' when no PDF library is present, and 'not evaluable' is never a pass.
    A relative `pdf_path` is resolved against RESULTS, where the report has to sit.
    """
    expected = [e for e in figures if e.get("file")]
    if not expected:
        return True, "no figures declared"
    try:
        import pypdf  # type: ignore

        reader = pypdf.PdfReader(str(_at_results(pdf_path)))
        n = sum(len(getattr(page, "images", []) or []) for page in reader.pages)
    except Exception as exc:                 # noqa: BLE001 - any failure is 'not evaluable'
        return False, f"NOT-EVALUABLE: could not count embedded images ({type(exc).__name__})"
    if n < len(expected):
        return False, f"report embeds {n} image(s) but {len(expected)} figure(s) were declared"
    return True, f"report embeds {n} image(s) for {len(expected)} declared figure(s)"


def assert_figure_ok(path: str | pathlib.Path, min_bytes: int = 5_000) -> None:
    """Fail on a blank or degenerate figure. A blank plot passing QC is a gate that cannot fail.

    A relative path is resolved against RESULTS.
    """
    p = _at_results(path)
    if not p.exists():
        raise GateFailure(f"figure not written: {p}")
    size = p.stat().st_size
    if size < min_bytes:
        raise GateFailure(f"figure {p.name} is {size} B — almost certainly blank")

    if p.suffix.lower() == ".svg":
        return
    try:
        from PIL import Image  # type: ignore

        with Image.open(p) as im:
            extrema = im.convert("L").getextrema()
        if extrema[0] == extrema[1]:
            raise GateFailure(f"figure {p.name} has a single pixel value — it is blank")
    except ImportError:
        pass  # size check already ran; do not install anything into a live session


# --- the brand gate ------------------------------------------------------------------------------

_DELEGATE = "pdf-report-generation"
_ACCENT = "PHYLO_GOLD"
# The FUNCTIONAL half of the palette, by name. The chart accents (PHYLO_BLUE, PHYLO_GREEN, PHYLO_LIME,
# PHYLO_ORANGE, PHYLO_PINK, PHYLO_BLACK) are left out deliberately, not by oversight: the unbranded
# report's embedded matplotlib figures paint PHYLO_BLUE and PHYLO_GREEN, so counting a chart accent
# would pass the very artifact this gate exists to reject.
#
# By NAME rather than by parsing the palette's `# Derived / functional` section comment. A section
# parse picks up a renamed colour for free, but it fails OPEN — reorder the palette so a chart accent
# lands under that comment and the trap above re-opens silently. A name list can only ever go narrow,
# and going narrow is caught by the two-marker floor in assert_report_branded. The cost is that a
# functional colour added to the palette has to be added here too, or it simply is not looked for.
_FUNCTIONAL = (_ACCENT, "HEADING_COLOR", "BODY_TEXT", "MUTED_TEXT", "TABLE_ALT_ROW", "TABLE_BORDER",
               "CALLOUT_BG", "DIVIDER_COLOR", "LINK_COLOR")
# Values still come from the file, so the palette stays the single source: move #111111 there and the
# gate demands the new value. Only direct HexColor literals — the alias forms (DIVIDER_COLOR =
# PHYLO_GOLD, CALLOUT_BG = PHYLO_OFF_WHITE) contribute nothing today, and following a name to a name
# could land on a chart accent. They are listed so a literal value would be read.
_HEXDEF = r'(?<!\w){}\s*=\s*HexColor\(\s*["\']#([0-9A-Fa-f]{{6}})["\']\s*\)'
# Colours that appear in branded and unbranded reports alike, so hitting one proves nothing. White is
# TABLE_HEADER_FG; black is here for the same reason and not because the palette names it today — the
# real unbranded report sets #000000 24 times, so the day a functional name is spelled as black rather
# than #111111, that report would score a marker it did not earn.
_NO_SIGNAL = ("#FFFFFF", "#000000")


def _palette_file(palette: str | pathlib.Path | None = None) -> pathlib.Path:
    """Where the brand palette lives at run time. Env var first, so a moved skills mount still works."""
    if palette is not None:
        return pathlib.Path(palette)
    root = os.environ.get("BIOMNI_SKILLS_ROOT") or "/mnt/skills/system"
    return pathlib.Path(root) / _DELEGATE / "SKILL.md"


def _rgb(hexv: str) -> tuple[int, int, int]:
    return int(hexv[1:3], 16), int(hexv[3:5], 16), int(hexv[5:7], 16)


def brand_markers(palette: str | pathlib.Path | None = None) -> dict[str, str]:
    """The functional palette colours this gate looks for, as {NAME: "#RRGGBB"}, accent first.

    Deliberately not pinned in this file: a hex literal here is a second copy of the palette, and it
    goes stale silently the day the brand moves. The delegate skill is the single source.

    A name defined twice with two different values is dropped rather than guessed at. For the accent
    that ambiguity is fatal, because the accent is mandatory; for the others it just costs a marker,
    and the floor in assert_report_branded catches the case where it costs the last one.
    """
    p = _palette_file(palette)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise GateFailure(
            f"cannot verify the report's branding: no palette at {p} ({type(exc).__name__}). "
            "An unverifiable brand is not a verified one. Set BIOMNI_SKILLS_ROOT or check that the "
            f"`{_DELEGATE}` skill is mounted; do not report the branding as checked."
        ) from exc

    def defined(name: str) -> set[str]:
        return {"#" + m.group(1).upper() for m in re.finditer(_HEXDEF.format(name), text)}

    found = defined(_ACCENT)
    if len(found) != 1:                      # 0 = palette changed shape; >1 = which one is the brand?
        raise GateFailure(
            f"cannot verify the report's branding: found {len(found)} {_ACCENT} definitions in {p}, "
            "need exactly one. An unverifiable brand is not a verified one — fix the palette source "
            "rather than assuming a value."
        )
    markers = {_ACCENT: found.pop()}
    for name in _FUNCTIONAL:
        if name == _ACCENT:
            continue
        vals = defined(name)
        if len(vals) == 1:
            markers[name] = vals.pop()
    return markers


def brand_accent(palette: str | pathlib.Path | None = None) -> tuple[int, int, int]:
    """The gold accent as (r, g, b). Raises if the palette cannot be read."""
    return _rgb(brand_markers(palette)[_ACCENT])


# Content streams are normally compressed, and ReportLab's default is /Filter [/ASCII85Decode
# /FlateDecode] — a85 stacked OVER flate. Decoding only the flate half yields zero bytes, which reads
# as "this PDF has no colour operators at all" and is a parser bug wearing a finding's clothes.
_STREAM_RE = re.compile(rb"(?<!end)stream\r?\n")     # 'endstream' contains 'stream'; do not match it
_FILTER_RE = re.compile(rb"/Filter\s*(/[A-Za-z0-9]+|\[[^\]]*\])")
_NUM = rb"[-+]?(?:\d{1,12}(?:\.\d{0,12})?|\.\d{1,12})"   # bounded: unbounded \d+ backtracks on noise
_RGB_RE = re.compile(rb"(?<![\w.])(%s)\s+(%s)\s+(%s)\s+(?:rg|RG)(?![\w.])" % (_NUM, _NUM, _NUM))
_GRAY_RE = re.compile(rb"(?<![\w.])(%s)\s+(?:g|G)(?![\w.])" % _NUM)
_CMYK_RE = re.compile(rb"(?<![\w.])(%s)\s+(%s)\s+(%s)\s+(%s)\s+(?:k|K)(?![\w.])" % ((_NUM,) * 4))
_NONTEXT_RE = re.compile(rb"[^\t\r\n\x20-\x7e]")


def _flate(b: bytes) -> bytes:
    """decompressobj, not decompress: a stream padded to /Length makes the one-shot form raise."""
    for wbits in (15, -15):                  # -15 = raw deflate, no zlib header
        obj = zlib.decompressobj(wbits)
        try:
            out = obj.decompress(b) + obj.flush()
        except zlib.error:
            continue
        if out:
            return out
    raise zlib.error("not a flate stream")


def _a85(b: bytes) -> bytes:
    b = re.sub(rb"\s", b"", b)
    stop = b.find(b"~>")                     # Adobe EOD; a85decode chokes on it
    return base64.a85decode(b[:stop] if stop >= 0 else b)


def _ahx(b: bytes) -> bytes:
    h = re.sub(rb"[^0-9A-Fa-f]", b"", b.split(b">")[0])
    return binascii.unhexlify(h + b"0" * (len(h) % 2))   # spec: a lone final digit is padded with 0


_DECODERS = {b"FlateDecode": _flate, b"Fl": _flate, b"ASCII85Decode": _a85, b"A85": _a85,
             b"ASCIIHexDecode": _ahx, b"AHx": _ahx}


def _content_streams(data: bytes) -> list[bytes]:
    """Every stream that decodes to something text-shaped. Images and font programs are skipped.

    Never parses the xref or follows /Contents: a colour operator anywhere in the drawing operators
    counts, and an object graph is a great deal of code to reach the same answer. Binary payloads are
    dropped twice over — by their object dict, then by a printability test — because regexing image
    bytes for `rg` costs minutes and finds noise.
    """
    out = []
    for m in _STREAM_RE.finditer(data):
        head = data[data.rfind(b"obj", 0, m.start()):m.start()]
        if b"/Image" in head or b"/Length1" in head or b"FontFile" in head:
            continue
        end = data.find(b"endstream", m.end())
        if end < 0:
            continue
        raw = data[m.end():end]
        if raw.endswith(b"\r\n"):            # the EOL before 'endstream' is not stream data
            raw = raw[:-2]
        elif raw[-1:] in (b"\n", b"\r"):
            raw = raw[:-1]

        fm = _FILTER_RE.search(head)         # /Filter is often an ARRAY, applied left to right
        names = re.findall(rb"/([A-Za-z0-9]+)", fm.group(1)) if fm else []
        try:
            for name in names:
                raw = _DECODERS[name](raw)   # KeyError = a filter we cannot undo, e.g. DCTDecode
        except (KeyError, ValueError, zlib.error, binascii.Error):
            continue
        sample = raw[:4096]
        if len(_NONTEXT_RE.findall(sample)) > len(sample) // 10:
            continue
        out.append(raw)
    return out


def pdf_colors(pdf_path: str | pathlib.Path) -> dict[str, int]:
    """Distinct colours set by a PDF's content streams, as {"#RRGGBB": times set}.

    Stdlib only, so this always reaches a verdict — unlike report_embeds_figures, which needs pypdf.
    A relative path is resolved against RESULTS.
    """
    data = _at_results(pdf_path).read_bytes()
    counts: dict[str, int] = {}

    def add(rgb: tuple[float, ...]) -> None:
        chans = tuple(round(v * 255) for v in rgb)
        if all(0 <= c <= 255 for c in chans):
            key = "#%02X%02X%02X" % chans
            counts[key] = counts.get(key, 0) + 1

    for stream in _content_streams(data):
        for m in _RGB_RE.finditer(stream):
            add(tuple(float(m.group(i)) for i in (1, 2, 3)))
        for m in _GRAY_RE.finditer(stream):
            add((float(m.group(1)),) * 3)
        # The palette is HexColor, so this is only a safety net against a CMYK restyling failing the
        # gate. (1-v)(1-k) is what a reader renders DeviceCMYK as, and it returns gold exactly;
        # ReportLab's own cmyk2rgb uses 1-min(1,v+k) instead, which does not.
        for m in _CMYK_RE.finditer(stream):
            k = float(m.group(4))
            add(tuple((1 - float(m.group(i))) * (1 - k) for i in (1, 2, 3)))
    return counts


def _within(hexv: str, accent: tuple[int, int, int], tol: int) -> bool:
    """All three channels or nothing. ReportLab writes 6 decimals (.831373 .627451 .290196), which
    round-trips exactly; 2 units absorbs a producer that writes only 2 or 3.

    Per channel, and never a luminance or single-channel match: #D5CFC5, the warm grey used for table
    grid lines, sits 1 unit from gold in RED — and 47 away in green, so all three cannot be reached.
    """
    chans = (int(hexv[1:3], 16), int(hexv[3:5], 16), int(hexv[5:7], 16))
    return all(abs(a - b) <= tol for a, b in zip(chans, accent))


def assert_report_branded(report_name: str, palette: str | pathlib.Path | None = None,
                          tol: int = 2) -> str:
    """Fail unless the report carries the Phylo accent AND at least one other functional palette
    colour — i.e. unless it really went through the `pdf-report-generation` skill.

    What this is: an exact-value provenance check with a rounding cushion of `tol`/255 per channel. It
    reads the colours the PDF's own vector operators set and asks whether they are the palette's
    values. What it is NOT: a perceptual or design check. It says nothing about whether the report
    looks Phylo, and a gold a designer would accept fails it when the numbers differ. It raises the
    cost of faking branding — it does not make it impossible, and it should not be read as proof.

    Why this exists: the twin of assert_generated_by_tool. That gate exists because a schematic drawn
    with matplotlib passes a visual check and is not an infographic. This one exists because a
    hand-styled ReportLab PDF passes every other check in this module — it exists, it is big enough,
    it embeds the figures — and is not a Phylo report. The delegate skill ships NO code, only a
    palette, so "used ReportLab" is evidence of nothing (the branded path uses ReportLab too) and
    "loaded the skill" is a proxy an agent can satisfy and then ignore. Hence a property of the
    ARTIFACT: the palette's colours are in the page's colour operators or they are not.

    Why two markers rather than one, measured on three artifacts: the branded report carries 6 of the
    functional palette colours, the real unbranded report 0, and that same unbranded report with one
    60x6pt gold rectangle stamped on it carries 1. At one marker the gate was a tripwire — a single
    `rg` operator out of 52, about 0.006% of the page, flipped the verdict on a PDF with a blue title
    and an olive divider. Two markers mean a faked pass has to paint several exact palette values, and
    an agent holding those values has read the palette, which was the goal.

    The accent stays mandatory rather than "any two functional colours": it is the primary accent and
    its absence is the strongest single signal. Measured on the unbranded report, gold is 95 units
    away from the nearest colour it contains, where the near-neutrals are 9 (#CCCCCC nearly reaches
    TABLE_BORDER, #F2F6FC nearly reaches TABLE_ALT_ROW). Open `tol` up and the neutrals start matching
    a plain grey report; gold does not, so gold is the marker worth making compulsory.

    `report_name` is basenamed to the results ROOT, exactly as assert_report_exists does: that is
    where the report is required to be, so that is the only file worth checking.
    """
    markers = brand_markers(palette)         # raises first: unverifiable is never a pass
    want = markers[_ACCENT]
    accent = _rgb(want)
    # Independent markers or none. An "other" whose value IS the accent (an alias spelled out as a
    # literal) would let one gold rectangle satisfy both halves; white appears in every report ever
    # written, branded or not.
    others = {n: h for n, h in markers.items()
              if n != _ACCENT and h not in _NO_SIGNAL and not _within(h, accent, tol)}
    if not others:
        raise GateFailure(
            f"cannot verify the report's branding: the palette at {_palette_file(palette)} leaves "
            f"this gate only the accent {want} to look for — of {', '.join(_FUNCTIONAL)} nothing else "
            "resolves to a colour of its own. A one-marker check is satisfied by a single gold "
            "rectangle, so this is a degrade, not a pass. Fix the palette source, or the name list in "
            "report_qc._FUNCTIONAL if the palette renamed something; an unverifiable brand is not a "
            "verified one."
        )

    base = os.path.basename(report_name)
    path = RESULTS / base
    if not path.exists():
        raise GateFailure(
            f"cannot verify branding: {base} is not at {RESULTS}/. Call assert_report_exists first."
        )

    found = pdf_colors(path)
    if not found:
        raise GateFailure(
            f"no colour operators could be read from {base}: the PDF sets no vector fill or stroke "
            "colour anywhere, so its branding cannot be verified, and an unverifiable brand is not a "
            "verified one. A rasterised or screenshot PDF lands here — colour inside an embedded "
            "image is deliberately not read, so a picture of a branded report does not count as one. "
            f"Build the report as real vector text and tables through the `{_DELEGATE}` skill."
        )

    looked_for = {_ACCENT: want, **others}
    hit = [n for n, h in looked_for.items() if any(_within(c, _rgb(h), tol) for c in found)]
    seen = sorted(found, key=lambda k: -found[k])
    used = ", ".join(seen[:12]) + (", ..." if len(seen) > 12 else "")

    if _ACCENT not in hit:
        # Derived, not pinned: the near-miss has to move when the accent or the tolerance does.
        near = "#%02X%02X%02X" % tuple(c + tol + 1 if c + tol + 1 <= 255 else c - tol - 1
                                       for c in accent)
        raise GateFailure(
            f"{base} does not use the Phylo accent {want} — checked as an exact value, within "
            f"{tol}/255 per channel, against the palette in `{_DELEGATE}`. That cushion absorbs a "
            f"producer that writes 2-decimal colours and nothing more: {near} is indistinguishable "
            "from the accent to the eye and is rejected here, so a gold that merely looks right does "
            "not satisfy this gate — the report has to carry the palette's own values. Delegate "
            f"report generation to `{_DELEGATE}` (load the skill and paint from its palette) rather "
            f"than styling one yourself. Colours it used instead: {used}"
        )

    if len(hit) < 2:
        raise GateFailure(
            f"{base} carries the accent {want} and nothing else from the palette. One colour is a "
            "stamp, not a branded report: the accent alone is satisfied by a single gold rectangle "
            "painted onto an otherwise hand-styled PDF, which is why two independent markers are "
            f"required. A report built through `{_DELEGATE}` paints its headings, body text, table "
            "rows and grid lines from the palette too — expected at least one of "
            + ", ".join(f"{n} {h}" for n, h in others.items())
            + f". Colours it used instead: {used}"
        )
    return want


# --- writing large binaries to the results mount -------------------------------------------------

def staged_copy(src: str | pathlib.Path, dst: str | pathlib.Path) -> pathlib.Path:
    """Write random-access formats to /workspace first, then shell-cp onto the results mount.

    Direct writes of .h5/.h5ad/.xlsx/.pptx/.db to the FUSE-backed results mount can truncate, and in
    R `file.copy()` onto that mount yields 0-byte files while reporting success. Use `cp`.

    A relative `dst` is resolved against RESULTS. `src` stays relative to the CWD — it is the
    workspace staging file, which is the whole point of the two-step write.
    """
    src, dst = pathlib.Path(src), _at_results(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(["cp", str(src), str(dst)], check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        shutil.copyfile(src, dst)
    if not dst.exists() or dst.stat().st_size == 0:
        raise GateFailure(f"staged copy produced a 0-byte file at {dst}")
    return dst


# --- the output-artifact contract gate (Review Stop 4A, finding 1) --------------------------------
# Why this exists: the v2 run declared its outputs -- variant-bearing CSVs, the WT/unspecified
# composition bucket, the docking-box provenance -- but NOTHING confirmed they were actually written
# with the declared content, so the review recorded "expected output artifacts were not confirmed".
# The infographic/report/figure/citation gates above never look at the data artifacts. This is the
# gate that confirms them. Call it in the terminal step BEFORE write_facts(), so a run that dropped
# or mis-shaped an artifact stops instead of shipping a report that references it. Its expectation
# comes from a package-shipped contract (assets/output_contract.json), which the run cannot rewrite.

_PKG_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _read_csv_header(path: pathlib.Path) -> tuple[list[str], int]:
    """Header field names + data-row count, stdlib only (no pandas needed inside the gate)."""
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        reader = csv.reader(fh)
        try:
            header = next(reader)
        except StopIteration:
            return [], 0
        n_rows = sum(1 for _ in reader)
    return [h.strip() for h in header], n_rows


def _run_json_contract_check(rel: str, obj, chk: str) -> None:
    if chk == "wt_unspecified_bucket":
        comp = obj.get("composition") if isinstance(obj, dict) else None
        labels = []
        if isinstance(comp, list):
            labels = [str(c.get("variant", "")) for c in comp if isinstance(c, dict)]
        elif isinstance(comp, dict):
            labels = [str(k) for k in comp]
        if not any(("WT" in l.upper()) or ("UNSPEC" in l.upper()) or ("WILD" in l.upper())
                   for l in labels):
            raise GateFailure(
                f"{rel}: variant composition has no wild-type/unspecified bucket "
                f"(labels: {labels}). WT/unspecified must be its own bucket.")
    elif chk == "pdb_selection_mode_rationale":
        sel = obj.get("pdb_selection") if isinstance(obj, dict) else None
        if not (isinstance(sel, dict) and sel.get("mode")):
            raise GateFailure(
                f"{rel}: pdb_selection.mode is missing -- how the receptor was chosen "
                "(user_file | user_pdb_id | auto) must be recorded as provenance.")
    else:
        raise GateFailure(f"{rel}: unknown output-contract check {chk!r}")


def assert_output_contract(contract=None, *, target: str | None = None, results_dir=None) -> dict:
    """Fail closed unless EVERY contract-declared output artifact exists at its declared path, is
    non-empty, and carries the declared columns/keys/content.

    ``contract`` is a list of artifact specs or a path to output_contract.json (defaults to the
    package's assets/output_contract.json, resolved next to this module so CWD does not matter).
    ``{target}`` in any path is substituted with ``target``. ``results_dir`` overrides the results
    mount so a fixture run can point the gate at a temp directory.

    Each spec: {"path": "...", "kind": "csv"|"json"|"file", "required_columns": [...],
    "required_keys": [...], "checks": ["wt_unspecified_bucket"|"pdb_selection_mode_rationale"],
    "min_rows": N, "min_bytes": N}. Returns a summary suitable for the facts artifact.
    """
    root = pathlib.Path(results_dir) if results_dir is not None else RESULTS
    if contract is None:
        contract = _PKG_ROOT / "assets" / "output_contract.json"
    if isinstance(contract, (str, pathlib.Path)):
        cpath = pathlib.Path(contract)
        if not cpath.is_absolute() and not cpath.exists():
            cpath = _PKG_ROOT / cpath
        specs = json.loads(pathlib.Path(cpath).read_text(encoding="utf-8"))
    else:
        specs = contract
    if isinstance(specs, dict) and "artifacts" in specs:
        specs = specs["artifacts"]
    if not isinstance(specs, list) or not specs:
        raise GateFailure("output contract is empty -- declare the artifacts the run must produce.")

    confirmed = []
    for spec in specs:
        rel = spec["path"]
        if "{target}" in rel:
            if not target:
                raise GateFailure(
                    f"output-contract path {rel!r} needs a target but none was given. "
                    "Call assert_output_contract(target=<TARGET>).")
            rel = rel.replace("{target}", target)
        p = root / rel
        if not p.exists():
            raise GateFailure(
                f"declared output artifact not found: {rel} (expected at {p}). Every "
                "contract-declared artifact must be written, at its declared path, before finishing.")
        size = p.stat().st_size
        if size < int(spec.get("min_bytes", 1)):
            raise GateFailure(
                f"declared output artifact {rel} is only {size} B -- an empty or stubbed artifact "
                "is not a confirmed one.")
        kind = spec.get("kind", "file")
        if kind == "csv":
            header, n_rows = _read_csv_header(p)
            missing = [c for c in spec.get("required_columns", []) if c not in header]
            if missing:
                raise GateFailure(
                    f"{rel} is missing required column(s) {missing}. Present: {header}. The declared "
                    "schema (including variant provenance) must be written, not silently dropped.")
            if n_rows < int(spec.get("min_rows", 0)):
                raise GateFailure(
                    f"{rel} has {n_rows} data row(s), fewer than the required {spec.get('min_rows')}.")
        elif kind == "json":
            try:
                obj = json.loads(p.read_text(encoding="utf-8"))
            except Exception as exc:
                raise GateFailure(f"{rel} is not valid JSON: {exc}")
            for k in spec.get("required_keys", []):
                if not (isinstance(obj, dict) and k in obj):
                    raise GateFailure(f"{rel} is missing required key {k!r}.")
            for chk in spec.get("checks", []):
                _run_json_contract_check(rel, obj, chk)
        confirmed.append({"path": rel, "bytes": size, "kind": kind})
    return {"n_confirmed": len(confirmed), "artifacts": confirmed}


# --- the post-staging delivery-receipt gate (family F04) -----------------------------------------
# Why this exists: assert_output_contract (above) confirms the DATA artifacts named in the contract.
# It does NOT confirm the whole delivery -- figures, provenance, the deterministic-test receipt, the
# per-file manifest -- nor that what shipped matches a receipt of what was promised. The v2 recovery
# review recorded final_delivery_and_output_manifest_drift: the declared output set could be
# finalized BEFORE deliverables were staged and could drift from what actually shipped. This gate is
# finalized only AFTER staging. It reconciles the on-disk result INVENTORY against BOTH the declared
# CONTRACT (artifacts) and the packaged delivery RECEIPT (report/table/figure/provenance/test/
# manifest categories in assets/output_contract.json -> "delivery_receipt"), and fails closed unless
# every required path resolves. Removing or renaming any receipt-named artifact after staging fails
# it. Call it in the terminal step, after staged_copy, before write_facts.

def _load_contract_doc(contract):
    """Return the parsed output_contract.json document (dict), resolving the default path."""
    if contract is None:
        contract = _PKG_ROOT / "assets" / "output_contract.json"
    if isinstance(contract, (str, pathlib.Path)):
        cpath = pathlib.Path(contract)
        if not cpath.is_absolute() and not cpath.exists():
            cpath = _PKG_ROOT / cpath
        return json.loads(pathlib.Path(cpath).read_text(encoding="utf-8"))
    return contract


def _resolve_receipt_entry(root: pathlib.Path, entry: dict, target: str | None):
    """Resolve one delivery-receipt entry to [(rel, size), ...] or raise GateFailure.

    An entry is either {"path": "...", "min_bytes": N} (one required file) or
    {"glob": "...", "min_count": N} (at least N non-empty matches). '{target}' is substituted.
    """
    if "path" in entry:
        rel = entry["path"].replace("{target}", target or "")
        p = root / rel
        if not p.exists():
            raise GateFailure(
                f"delivery receipt names {rel!r} but it is not on disk at {p}. Every "
                "report/table/figure/provenance/test/manifest path in the receipt must resolve "
                "after staging (removing or renaming a receipt-named artifact fails this gate).")
        size = p.stat().st_size
        if size < int(entry.get("min_bytes", 1)):
            raise GateFailure(f"delivery-receipt artifact {rel} is only {size} B (empty/stubbed).")
        return [(rel, size)]
    if "glob" in entry:
        pat = entry["glob"].replace("{target}", target or "")
        nonempty = [(str(p.relative_to(root)), p.stat().st_size)
                    for p in sorted(root.glob(pat)) if p.is_file() and p.stat().st_size > 0]
        if len(nonempty) < int(entry.get("min_count", 1)):
            raise GateFailure(
                f"delivery receipt requires >= {entry.get('min_count', 1)} non-empty file(s) "
                f"matching {pat!r}; found {len(nonempty)}. A required deliverable category "
                "(figure/provenance) did not resolve after staging.")
        return nonempty
    raise GateFailure(f"malformed delivery-receipt entry (need 'path' or 'glob'): {entry!r}")


def assert_delivery_receipt(contract=None, *, target: str | None = None, results_dir=None) -> dict:
    """Fail closed unless the STAGED result inventory matches the declared contract AND the receipt.

    Three-way reconciliation (family F04,
    post_staging_result_inventory_matches_declared_contract_and_receipt):
      1. every contract-declared artifact exists with the declared content (delegates to
         assert_output_contract);
      2. every delivery-receipt path/glob (report/table/figure/provenance/test/manifest) resolves
         on disk, non-empty;
      3. every contract artifact is named by the receipt (declared contract is a subset of receipt).

    Finalize only AFTER staging deliverables. Returns the reconciled receipt for the facts artifact.
    """
    root = pathlib.Path(results_dir) if results_dir is not None else RESULTS
    doc = _load_contract_doc(contract)
    # 1) contract artifacts present with declared content.
    contract_summary = assert_output_contract(doc, target=target, results_dir=results_dir)
    # 2) delivery-receipt categories all resolve.
    receipt_spec = (doc.get("delivery_receipt") if isinstance(doc, dict) else None) or {}
    categories = receipt_spec.get("categories") or {}
    required = ["report", "table", "figure", "provenance", "test", "manifest"]
    missing_cat = [c for c in required if c not in categories]
    if missing_cat:
        raise GateFailure(
            f"delivery_receipt is missing required categor(y/ies) {missing_cat}. The receipt must "
            "cover report/table/figure/provenance/test/manifest so the whole delivery is confirmed.")
    resolved, receipt_paths = {}, set()
    for cat in required:
        cat_files = []
        for entry in categories[cat]:
            for rel, size in _resolve_receipt_entry(root, entry, target):
                cat_files.append({"path": rel, "bytes": size})
                receipt_paths.add(rel)
        resolved[cat] = cat_files
    # 3) declared contract must be a subset of the receipt.
    not_in_receipt = [a["path"] for a in contract_summary["artifacts"] if a["path"] not in receipt_paths]
    if not_in_receipt:
        raise GateFailure(
            f"contract artifact(s) {not_in_receipt} are not named in the delivery receipt -- the "
            "inventory, the declared contract, and the receipt must agree.")
    return {"n_contract_artifacts": contract_summary["n_confirmed"],
            "n_receipt_files": len(receipt_paths),
            "categories": {c: len(resolved[c]) for c in required},
            "receipt": resolved}


# --- the docking-box conformation/permissiveness gate (family F10) --------------------------------
# Why this exists: the docking box is built from a co-crystal ligand (prepare_docking.build_box_info).
# When that ligand is large / the box is oversized, the search becomes permissive: poses are scored
# across a wide region instead of one focused pocket -- a known driver of weak docking-score vs
# experimental-potency correlation, and a signal that the box may be defined by a ligand whose
# binding mode/conformation differs from the docked ligand class. The review recorded that this
# driver was not derived or surfaced (only a generic single-conformation caveat existed).
# prepare_docking.box_diagnostics DERIVES it (box volume, max axis, permissiveness) into
# docking_box.json; this gate fails closed if a permissive box does not carry the surfaced caveat,
# so the derivation is enforced, not left to prose.

def assert_docking_box_diagnostics(box=None, *, results_dir=None) -> dict:
    """Fail closed unless docking_box.json carries derived box diagnostics AND, when the box is
    permissive, a surfaced docking caveat naming the permissiveness/conformation driver.

    ``box`` may be a dict (fixture) or None to read docking_box.json from ``results_dir``/RESULTS.
    """
    if box is None:
        root = pathlib.Path(results_dir) if results_dir is not None else RESULTS
        p = root / "docking_box.json"
        if not p.exists():
            raise GateFailure(f"docking_box.json not found at {p}; run prepare_docking.py first.")
        try:
            box = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            raise GateFailure(f"docking_box.json is not valid JSON: {exc}")
    required = ["box_volume_A3", "box_max_dim_A", "box_permissive", "docking_caveats"]
    missing = [k for k in required if k not in box]
    if missing:
        raise GateFailure(
            f"docking_box.json lacks derived diagnostic field(s) {missing}. prepare_docking."
            "box_diagnostics must record box_volume_A3, box_max_dim_A, box_permissive and "
            "docking_caveats so receptor-conformation/box permissiveness is provable from the artifact.")
    caveats = box.get("docking_caveats") or []
    if not isinstance(caveats, list):
        raise GateFailure("docking_box.json 'docking_caveats' must be a list.")
    if box.get("box_permissive"):
        if not any("permissive" in str(c).lower() for c in caveats):
            raise GateFailure(
                "docking box is permissive (box_permissive=true) but no docking caveat names the "
                "permissiveness/conformation driver. A permissive box must surface the caveat that "
                "the box scores poses across a wide region and may not match the docked ligand's "
                "binding mode -- state receptor conformation and binding-site relevance in the report.")
    return {"box_volume_A3": box["box_volume_A3"], "box_max_dim_A": box["box_max_dim_A"],
            "box_permissive": bool(box["box_permissive"]), "n_caveats": len(caveats)}


# --- the facts artifact --------------------------------------------------------------------------

def write_facts(path: str | pathlib.Path, facts: dict) -> pathlib.Path:
    """Write the numbers the report is allowed to quote. Call this AFTER every gate has passed.

    The renderer must read from this file rather than restating what the agent remembers. A report
    that prints its count by reading the table cannot disagree with the table.

    A relative path is resolved against RESULTS: facts written under the CWD are invisible to both
    the report builder and the user.
    """
    p = _at_results(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(facts, indent=2, sort_keys=True), encoding="utf-8")
    return p


# --- the citation-resolution gate ----------------------------------------------------------------
# Why this exists: an EGFR run shipped a report whose bibliography was renumbered to 7 entries while
# inline citations kept session-global references.jsonl indices ([8]/[9]/[16] resolved to nothing;
# [5]/[6] bound to the wrong papers), and every blocking gate passed because none checked citations.
# The durable fix moves numbering into ONE keyed transform (scripts/render_citations.py), which
# writes citation_map.json as a single source of truth. This gate re-validates the FINAL rendered
# report against that map: it is the "cannot be silently rewritten" expectation the report-integrity
# design requires. The map is produced BEFORE the PDF is assembled, so it is not derived from the
# artifact it checks. Twin of assert_report_branded/assert_generated_by_tool: a property of the
# artifact, not a proxy for having run a step.

_CITE_GROUP_RE = re.compile(r"\[\s*(\d+(?:\s*,\s*\d+)*)\s*\]")
_CITE_INT_RE = re.compile(r"\d+")
_REFHEAD_RE = re.compile(r"(?im)^[\s#>*_-]*(references|bibliography|works cited)\b.*$")
# A bibliography line: "[3] ...", "3. ...", or "3) ..." at line start.
_BIB_LINE_RE = re.compile(r"(?m)^[\s>*_-]*(?:\[(\d+)\]|(\d+)[.)])\s+(.*\S)")


def _extract_pdf_text(path: pathlib.Path) -> str:
    """Extract text from a PDF for citation checking. Needs pypdf (declared preinstalled on Biomni).

    Absence is a real gap for a PDF input -- unlike the infographic-embedding check, there is no
    stdlib fallback that proves resolution -- so it fails closed with actionable guidance rather
    than passing unverified.
    """
    try:
        from pypdf import PdfReader
    except Exception as exc:                       # pragma: no cover - environment dependent
        raise GateFailure(
            f"cannot read {path.name} to verify citations: pypdf is unavailable ({exc}). Pass the "
            "already-extracted report text to assert_citations_resolve(report_is_text=True), or "
            "install pypdf. An unverifiable citation set is not a verified one."
        )
    try:
        reader = PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as exc:
        raise GateFailure(f"failed to extract text from {path.name}: {exc}")


def _read_report_text(report) -> str:
    """Resolve `report` to text: a PDF path (extracted), any other existing file (read), else raw."""
    if isinstance(report, pathlib.Path) or (isinstance(report, str) and len(report) < 400):
        cand = _at_results(report)
        if cand.exists() and cand.is_file():
            if cand.suffix.lower() == ".pdf":
                return _extract_pdf_text(cand)
            return cand.read_text(encoding="utf-8", errors="replace")
        base = RESULTS / os.path.basename(str(report))
        if base.exists() and base.is_file():
            return _extract_pdf_text(base) if base.suffix.lower() == ".pdf" else \
                base.read_text(encoding="utf-8", errors="replace")
    return str(report)


def _split_body_refs(text: str) -> tuple[str, str]:
    m = _REFHEAD_RE.search(text)
    return (text, "") if not m else (text[: m.start()], text[m.end():])


def _parse_bibliography(refs: str) -> dict[int, str]:
    """label -> bibliography line text. A repeated label is a defect (raised by the caller)."""
    out: dict[int, list[str]] = {}
    for m in _BIB_LINE_RE.finditer(refs):
        label = int(m.group(1) or m.group(2))
        out.setdefault(label, []).append(m.group(3).strip())
    dupes = sorted(k for k, v in out.items() if len(v) > 1)
    if dupes:
        raise GateFailure(f"bibliography lists label(s) {dupes} more than once")
    return {k: v[0] for k, v in out.items()}


def assert_citations_resolve(report, citation_map="citation_map.json", *, report_is_text=False) -> dict:
    """Fail unless every inline [N] in the report resolves to exactly one bibliography entry bound
    to the identity recorded in ``citation_map`` (the single source from render_citations).

    Fails closed (GateFailure) on: an inline label with no entry (missing/stale/sparse -- the 16->7
    failure); an inline label above the entry count; non-contiguous 1..N labels; a bibliography label
    used twice; distinct-inline-label set != bibliography-label set (orphan or dropped entry); the map
    disagreeing with the rendered entry count; or a label whose rendered entry does not carry the
    identity token the map bound to it (misbound -- catches reordered/wrong-paper bibliographies).

    ``report`` may be raw text (report_is_text=True), a PDF path, or a text-file path.
    ``citation_map`` may be a dict or a path to citation_map.json.
    """
    text = report if report_is_text else _read_report_text(report)
    cmap = citation_map if isinstance(citation_map, dict) else \
        json.loads(_at_results(citation_map).read_text(encoding="utf-8"))

    token_by_label = {int(k): v for k, v in cmap.get("token_by_label", {}).items()}
    n_map = int(cmap.get("n_bibliography", len(token_by_label)))
    if not token_by_label:
        raise GateFailure(
            "citation_map has no token_by_label -- it was not produced by render_citations. "
            "Render citations through scripts/render_citations.py and write citation_map.json."
        )

    body, refs = _split_body_refs(text)
    if not refs.strip():
        raise GateFailure(
            "no References/Bibliography section found in the report. A literature-grounded report "
            "must carry a References section its inline [N] citations resolve to."
        )

    inline = [int(t) for g in _CITE_GROUP_RE.finditer(body)
              for t in _CITE_INT_RE.findall(g.group(1))]
    inline_set = set(inline)
    if not inline_set:
        raise GateFailure("no inline [N] citations found in the report body")

    biblio = _parse_bibliography(refs)
    biblio_labels = set(biblio)

    # 1. bibliography must be contiguous 1..N and agree with the map's count.
    if sorted(biblio_labels) != list(range(1, len(biblio) + 1)):
        raise GateFailure(f"bibliography labels are not contiguous 1..N: {sorted(biblio_labels)}")
    if len(biblio) != n_map:
        raise GateFailure(
            f"bibliography has {len(biblio)} entries but citation_map records {n_map}. The report "
            "was not built from the rendered bibliography."
        )

    # 2. no stale/sparse inline label -- the 16->7 failure lands here.
    stale = sorted(x for x in inline_set if x < 1 or x > len(biblio) or x not in biblio_labels)
    if stale:
        raise GateFailure(
            f"inline citation(s) {stale} do not resolve to any of the {len(biblio)} bibliography "
            "entries -- a session-global or dropped index survived. Re-render through "
            "render_citations.py so inline labels and the bibliography share one map."
        )

    # 3. every bibliography entry must be cited, and vice-versa (single source, no orphans).
    orphan = sorted(biblio_labels - inline_set)
    if orphan:
        raise GateFailure(
            f"bibliography entr(y/ies) {orphan} are never cited in the body. Every listed reference "
            "must have an in-text citation; every citation must have a reference."
        )

    # 4. identity binding: the entry under label k must carry the identity token the map bound to k.
    misbound = []
    for label, token in token_by_label.items():
        line = biblio.get(label, "")
        if token and token.lower() not in line.lower():
            misbound.append(label)
    if misbound:
        raise GateFailure(
            f"bibliography label(s) {sorted(misbound)} are bound to the wrong source: the rendered "
            "entry does not carry the identity token citation_map assigned to that label (a "
            "reordered or misbound bibliography -- e.g. [6] pointing to a different paper). Rebuild "
            "the References block from render_citations() output, in label order."
        )

    return {
        "n_inline_distinct": len(inline_set),
        "n_bibliography": len(biblio),
        "labels_contiguous": True,
        "identity_bound": True,
    }
