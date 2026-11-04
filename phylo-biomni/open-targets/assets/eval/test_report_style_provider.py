#!/usr/bin/env python3
"""Provider-selection and provider-aware PDF gate regression tests."""

from __future__ import annotations

import json
import pathlib
import sys
import unittest


PACKAGE = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE / "scripts"))

import report_qc  # noqa: E402
import report_content  # noqa: E402


def _provider(
    root: pathlib.Path,
    name: str,
    *,
    activation: str,
    required: str,
    supporting: str,
    aliases: list[str] | None = None,
) -> None:
    directory = root / name
    (directory / "assets").mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test provider\n---\n# {name}\n",
        encoding="utf-8",
    )
    profile = {
        "schema": "biomni-report-style/1",
        "provider": name,
        "activation": activation,
        "pdf_markers": {
            "required_any": [required],
            "supporting_any": [supporting],
            "minimum_distinct_markers": 2,
        },
    }
    if aliases is not None:
        profile["user_selection_aliases"] = aliases
    (directory / "assets" / "report_style.json").write_text(
        json.dumps(profile), encoding="utf-8"
    )


def _roots(tmp_path: pathlib.Path) -> tuple[pathlib.Path, ...]:
    user = tmp_path / "user"
    system = tmp_path / "system"
    user.mkdir()
    system.mkdir()
    _provider(
        system,
        "pdf-report-generation",
        activation="default",
        required="#D4A04A",
        supporting="#111111",
    )
    _provider(
        user,
        "example-enterprise-styling",
        activation="explicit_only",
        required="#0066F5",
        supporting="#071D49",
        aliases=["example enterprise styling", "example enterprise house style", "example enterprise template"],
    )
    _provider(
        user,
        "bms-styling",
        activation="explicit_only",
        required="#BE2BBB",
        supporting="#563D82",
        aliases=["bms styling", "bms house style", "bms template"],
    )
    return user, system


def _transcript(path: pathlib.Path, records: list[dict]) -> pathlib.Path:
    path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
    return path


def _pdf(path: pathlib.Path, colors: list[str]) -> None:
    operators = []
    for color in colors:
        rgb = tuple(int(color[index:index + 2], 16) / 255 for index in (1, 3, 5))
        operators.append("%.6f %.6f %.6f rg" % rgb)
    stream = "\n".join(operators).encode("ascii")
    path.write_bytes(
        b"%PDF-1.4\n1 0 obj\n<< /Length "
        + str(len(stream)).encode("ascii")
        + b" >>\nstream\n"
        + stream
        + b"\nendstream\nendobj\n%%EOF\n"
    )


class ReportStyleProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._temporary = tempfile.TemporaryDirectory()
        self.tmp_path = pathlib.Path(self._temporary.name)
        self.original_results = report_qc.RESULTS

    def tearDown(self) -> None:
        report_qc.RESULTS = self.original_results
        self._temporary.cleanup()

    def test_missing_transcript_uses_package_default(self) -> None:
        resolved = report_qc.report_style_profile(
            transcript=self.tmp_path / "missing.jsonl", roots=_roots(self.tmp_path)
        )
        self.assertEqual(resolved["provider"], "pdf-report-generation")
        self.assertEqual(resolved["selection"], "package_default")

    def test_customer_context_does_not_select_enterprise_style(self) -> None:
        transcript = _transcript(
            self.tmp_path / "trace.jsonl",
            [{"type": "user", "id": "m1", "content": "Analyze this ExampleCorp project dataset."}],
        )
        resolved = report_qc.report_style_profile(
            transcript=transcript, roots=_roots(self.tmp_path)
        )
        self.assertEqual(resolved["provider"], "pdf-report-generation")

    def test_explicit_user_selection_owns_canonical_pdf(self) -> None:
        roots = _roots(self.tmp_path)
        transcript = _transcript(
            self.tmp_path / "trace.jsonl",
            [{"type": "user", "id": "m1", "content": "Use the example enterprise house style for the PDF."}],
        )
        resolved = report_qc.report_render_plan(transcript=transcript, roots=roots)
        self.assertEqual(resolved["provider"], "example-enterprise-styling")
        self.assertEqual(resolved["selection"], "explicit_override")
        self.assertFalse(resolved["render_with_bundled"])

        results = self.tmp_path / "results"
        results.mkdir()
        report_qc.RESULTS = results
        _pdf(results / "report.pdf", ["#0066F5", "#071D49"])
        evidence = report_qc.assert_report_styled("report.pdf", resolved_style=resolved)
        self.assertEqual(evidence["provider"], "example-enterprise-styling")
        self.assertTrue(evidence["pdf_sha256"])
        default = report_qc.report_style_profile(
            transcript=self.tmp_path / "missing.jsonl", roots=roots
        )
        with self.assertRaisesRegex(report_qc.GateFailure, "required marker"):
            report_qc.assert_report_styled("report.pdf", resolved_style=default)

    def test_assistant_only_selection_is_ignored(self) -> None:
        transcript = _transcript(
            self.tmp_path / "trace.jsonl",
            [{"type": "assistant", "id": "m1", "content": "Use the example enterprise styling skill."}],
        )
        resolved = report_qc.report_style_profile(
            transcript=transcript, roots=_roots(self.tmp_path)
        )
        self.assertEqual(resolved["provider"], "pdf-report-generation")

    def test_user_can_revoke_explicit_selection(self) -> None:
        transcript = _transcript(
            self.tmp_path / "trace.jsonl",
            [
                {"type": "user", "id": "m1", "content": "Use example enterprise styling for the PDF."},
                {"type": "user", "id": "m2", "content": "Do not use example enterprise styling."},
            ],
        )
        resolved = report_qc.report_style_profile(
            transcript=transcript, roots=_roots(self.tmp_path)
        )
        self.assertEqual(resolved["provider"], "pdf-report-generation")
        self.assertEqual(resolved["selection"], "package_default")

    def test_conflicting_user_selection_fails_closed(self) -> None:
        transcript = _transcript(
            self.tmp_path / "trace.jsonl",
            [{"type": "user", "id": "m1", "content": "Use example enterprise styling and BMS styling."}],
        )
        with self.assertRaisesRegex(report_qc.GateFailure, "conflicting"):
            report_qc.report_style_profile(transcript=transcript, roots=_roots(self.tmp_path))

    def test_selected_malformed_provider_fails_closed(self) -> None:
        roots = _roots(self.tmp_path)
        broken = roots[0] / "broken-styling"
        (broken / "assets").mkdir(parents=True)
        (broken / "SKILL.md").write_text(
            "---\nname: broken-styling\n---\n# Broken\n", encoding="utf-8"
        )
        (broken / "assets" / "report_style.json").write_text(
            json.dumps({
                "schema": "wrong",
                "provider": "broken-styling",
                "activation": "explicit_only",
                "user_selection_aliases": ["broken styling"],
            }),
            encoding="utf-8",
        )
        transcript = _transcript(
            self.tmp_path / "trace.jsonl",
            [{"type": "user", "id": "m1", "content": "Use broken styling for the PDF."}],
        )
        with self.assertRaisesRegex(report_qc.GateFailure, "unavailable"):
            report_qc.report_style_profile(transcript=transcript, roots=roots)

    def test_report_content_preserves_scientific_objects_exactly(self) -> None:
        facts = {
            "disease_id": "MONDO_0004975",
            "top_targets": [{"approved_symbol": "IL33", "overall_association_score": 0.812}],
            "credible_sets": [{"variant_id": "1_100_A_G", "pvalue": 1.2e-12}],
            "caveats": ["Association scores are prioritization metrics, not causal proof."],
        }
        provenance = {"source_mode": "fixture", "release_label": "24.12"}
        (self.tmp_path / "report_facts.json").write_text(json.dumps(facts), encoding="utf-8")
        (self.tmp_path / "provenance.json").write_text(json.dumps(provenance), encoding="utf-8")
        content = report_content.build_report_content(self.tmp_path)
        self.assertEqual(content["facts"], facts)
        self.assertEqual(content["provenance"], provenance)
        self.assertIsNone(content["presentation"])

    def test_finalization_binds_one_canonical_pdf_and_style_free_content(self) -> None:
        results = self.tmp_path / "results"
        results.mkdir()
        report_qc.RESULTS = results
        ledger = results / "operation_ledger.json"
        ledger.write_text('{"operations":[]}', encoding="utf-8")
        operation_id = report_qc._sha256(ledger)
        facts = {
            "source_mode": "fixture",
            "operation_ledger_sha256": operation_id,
            "disease_id": "MONDO_0004975",
            "studies": [],
            "top_targets": [],
        }
        provenance = {
            "source_mode": "fixture",
            "operation_ledger_sha256": operation_id,
        }
        (results / "report_facts.json").write_text(json.dumps(facts), encoding="utf-8")
        (results / "provenance.json").write_text(json.dumps(provenance), encoding="utf-8")
        infographic = results / "infographic_open_targets_workflow.png"
        infographic.write_bytes(b"png-bytes")
        report_qc.finalize_root_bundle([
            "operation_ledger.json", "report_facts.json", "provenance.json",
            "infographic_open_targets_workflow.png",
        ])
        report_content.write_report_content(results)
        report = results / "report_open-targets.pdf"
        report.write_bytes(b"%PDF-canonical")
        report_qc.finalize_report_artifacts(report.name)
        bundle = json.loads((results / "root_bundle.json").read_text(encoding="utf-8"))
        self.assertIn("report_open-targets.pdf", bundle["artifacts"])
        self.assertIn("report_content.json", bundle["artifacts"])
        self.assertIn("report_structure.json", bundle["artifacts"])
        report.write_bytes(b"%PDF-overwritten")
        with self.assertRaisesRegex(report_qc.GateFailure, "drift"):
            report_qc.assert_root_bundle_lineage(
                "root_bundle.json",
                ["report_open-targets.pdf", "report_content.json", "report_structure.json"],
            )

    def test_explicit_provider_finalize_phase_cannot_repeat_scientific_queries(self) -> None:
        analysis = (PACKAGE / "scripts" / "run_analysis.py").read_text(encoding="utf-8")
        finalizer = (PACKAGE / "scripts" / "finalize_report.py").read_text(encoding="utf-8")
        self.assertLess(analysis.index("if content_only:"), analysis.index("report_render_plan()"))
        self.assertNotIn("OpenTargetsClient", finalizer)
        self.assertNotIn("client.request", finalizer)


if __name__ == "__main__":
    unittest.main()
