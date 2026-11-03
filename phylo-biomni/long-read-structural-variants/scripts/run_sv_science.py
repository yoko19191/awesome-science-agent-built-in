#!/usr/bin/env python3
"""Internal dual-caller SV computation invoked by the report-gated entrypoint."""
from __future__ import annotations
import argparse, json, os, pathlib, shutil, sys, tempfile
import matplotlib
matplotlib.use("Agg")
matplotlib.rcParams["font.family"]=["Liberation Sans","Arimo","DejaVu Sans"]
import matplotlib.pyplot as plt

sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import input_selection
import license_audit
import science_contract
import svlib

CLASS_ORDER=("DEL","INS","DUP","INV","BND","CNV","OTHER")
def _classes(values): return [name for name in CLASS_ORDER if name in values]+sorted(set(values)-set(CLASS_ORDER))
def _save(fig,root,stem):
    path=root/f"{stem}.png"; fig.savefig(path,dpi=150,bbox_inches="tight"); fig.savefig(root/f"{stem}.svg",bbox_inches="tight"); plt.close(fig); return str(path)
def _fig_counts(counts_a,counts_b,name_b,root):
    labels=_classes(set(counts_a)|set(counts_b)) or ["no calls"]; x=list(range(len(labels)))
    fig,axis=plt.subplots(figsize=(6,4)); axis.bar([i-.2 for i in x],[counts_a.get(k,0) for k in labels],.4,label="sniffles2",color="#0072B2"); axis.bar([i+.2 for i in x],[counts_b.get(k,0) for k in labels],.4,label=name_b,color="#E69F00"); axis.set_xticks(x,labels); axis.set_ylabel("SV count"); axis.set_xlabel("SV class"); axis.legend()
    return _save(fig,root,"figure_2_call_svs")
def _fig_sizes(calls,root):
    sizes=[max(1,call["svlen"]) for call in calls if call["svtype"]!="BND"]
    fig,axis=plt.subplots(figsize=(6,4))
    if sizes:
        import numpy as np
        bins=np.logspace(np.log10(50),np.log10(max(max(sizes),100)*1.2),18); axis.hist(sizes,bins=bins,color="#56B4E9",edgecolor="black",linewidth=.4); axis.set_xscale("log")
    axis.set_xlabel("SV length (bp, log scale)"); axis.set_ylabel("count")
    return _save(fig,root,"figure_3_normalize_and_index")
def _fig_concordance(rows,name_b,root):
    labels=_classes(rows) or ["no calls"]; shared=[rows.get(k,{}).get("shared",0) for k in labels]; a=[rows.get(k,{}).get("a_unique",0) for k in labels]; b=[rows.get(k,{}).get("b_unique",0) for k in labels]; x=list(range(len(labels)))
    fig,axis=plt.subplots(figsize=(6,4)); axis.bar(x,shared,label="shared",color="#009E73"); axis.bar(x,a,bottom=shared,label="sniffles2 only",color="#0072B2"); axis.bar(x,b,bottom=[s+u for s,u in zip(shared,a)],label=f"{name_b} only",color="#E69F00"); axis.set_xticks(x,labels); axis.set_ylabel("SV count"); axis.set_xlabel("SV class"); axis.legend()
    return _save(fig,root,"figure_4_compare_callers_concordance")
def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-mode",required=True,choices=["analysis"]); parser.add_argument("--input-selection",required=True)
    parser.add_argument("--bam",required=True); parser.add_argument("--reference",required=True); parser.add_argument("--run-json",required=True)
    parser.add_argument("--identity-check",required=True)
    parser.add_argument("--second-caller",required=True,choices=["cutesv","user_vcf"])
    parser.add_argument("--second-caller-vcf")
    parser.add_argument("--platform",default="ont",choices=["ont","hifi"]); parser.add_argument("--threads",type=int,default=1)
    parser.add_argument("--out-root",required=True); parser.add_argument("--evidence-tier",default="hypothesis_generating")
    args=parser.parse_args(argv)
    try:
        selected_paths={"bam":args.bam,"reference":args.reference,"run_json":args.run_json,
                        "upstream_identity_check":args.identity_check}
        if args.second_caller=="user_vcf":
            if not args.second_caller_vcf: parser.error("--second-caller user_vcf requires --second-caller-vcf")
            selected_paths["second_caller_vcf"]=args.second_caller_vcf
        elif args.second_caller_vcf:
            parser.error("--second-caller-vcf is accepted only with --second-caller user_vcf")
        selected=input_selection.assert_selected(args.input_selection,selected_paths)
        out=pathlib.Path(args.out_root).resolve()
        if out!=pathlib.Path(selected["result_root"]): parser.error("--out-root must equal sealed result_root")
        run_meta=svlib.load_run_metadata(args.run_json)
        if not run_meta.get("reference_build"): raise ValueError("run metadata lacks reference_build")
        upstream_identity=svlib.load_upstream_identity(args.identity_check,run_json_path=args.run_json)
        svlib.validate_platform_choice(upstream_identity,args.platform)
        support=svlib.coverage_scaled_support(upstream_identity)
    except (OSError,ValueError,input_selection.SelectionError) as exc:
        print(f"INPUT ERROR: {exc}",file=sys.stderr); return 2
    sv=out/"sv"; figures=out/"figures"; sv.mkdir(); figures.mkdir()
    work=pathlib.Path(tempfile.mkdtemp(prefix="svrun_"))
    contract=pathlib.Path(__file__).resolve().parents[1]/"skill_contract.json"
    try:
        license_audit.audit_and_gate(contract,str(sv/"dependency_licenses.json"),required=license_audit.DEFAULT_REQUIRED,strict=True)
    except license_audit.GateFailure as exc:
        shutil.rmtree(work,ignore_errors=True); print(f"COMMERCIAL GATE FAILED: {exc}",file=sys.stderr); return 4
    identity=svlib.reference_build_consistency(args.bam,args.reference,run_meta)
    (sv/"identity_check.json").write_text(json.dumps(identity,indent=2))
    if identity["reference_build_mismatch_violations"]:
        (sv/"not_computable.json").write_text(json.dumps({"status":"not_computable","reason":identity["detail"]},indent=2)); shutil.rmtree(work,ignore_errors=True); return 3
    raw=str(work/"sniffles.vcf"); svlib.call_sniffles(
        args.bam,args.reference,raw,threads=args.threads,
        min_support=support["sniffles2_min_support"])
    normalized=svlib.normalize_vcf(raw,args.reference,str(work/"calls_sniffles2.norm.vcf.gz"))
    if args.second_caller=="cutesv":
        second_raw=str(work/"cutesv.vcf"); svlib.call_cutesv(
            args.bam,args.reference,second_raw,str(work/"cutesv_work"),threads=args.threads,
            platform=args.platform,min_support=support["cutesv_min_support"]); second_name="cuteSV"
    else:
        second_raw=args.second_caller_vcf; second_name="user_vcf"
    second_normalized=svlib.normalize_vcf(second_raw,args.reference,str(work/"calls_second.norm.vcf.gz"))
    valid=svlib.is_valid_indexed_vcf(normalized) and svlib.is_valid_indexed_vcf(second_normalized)
    if not valid:
        shutil.rmtree(work,ignore_errors=True)
        print("NOT_COMPUTABLE: normalized caller VCFs are not both valid and indexed",file=sys.stderr)
        return 3
    try:
        calls=svlib.read_sv_vcf(normalized); second_calls=svlib.read_sv_vcf(second_normalized)
    except ValueError as exc:
        shutil.rmtree(work,ignore_errors=True)
        print(f"INPUT ERROR: {exc}",file=sys.stderr)
        return 2
    counts=svlib.counts_by_class(calls); second_counts=svlib.counts_by_class(second_calls); agreement=svlib.concordance(calls,second_calls)
    with (sv/"sv_counts.tsv").open("w") as handle:
        handle.write("caller\tsv_class\tcount\n")
        for category,count in sorted(counts.items()): handle.write(f"sniffles2\t{category}\t{count}\n")
        for category,count in sorted(second_counts.items()): handle.write(f"{second_name}\t{category}\t{count}\n")
    with (sv/"sv_calls.tsv").open("w") as handle:
        fields=("caller","chrom","pos","end","svtype","svlen","id","filter","quality","sample_id",
                "genotype","genotype_quality","read_support")
        handle.write("\t".join(fields)+"\n")
        for caller,records in (("sniffles2",calls),(second_name,second_calls)):
            for call in records:
                values=[caller]+[call.get(field) for field in fields[1:]]
                handle.write("\t".join("" if value is None else str(value) for value in values)+"\n")
    with (sv/"concordance.tsv").open("w") as handle:
        handle.write(f"sv_class\tsniffles2\t{second_name}\tshared\tsniffles2_unique\t{second_name}_unique\n")
        for category,row in sorted(agreement["by_class"].items()): handle.write(f"{category}\t{row['a']}\t{row['b']}\t{row['shared']}\t{row['a_unique']}\t{row['b_unique']}\n")
        row=agreement["totals"]; handle.write(f"ALL\t{row['a']}\t{row['b']}\t{row['shared']}\t{row['a_unique']}\t{row['b_unique']}\n")
    versions=svlib.tool_versions()
    provenance={"execution_mode":"analysis","requested_dataset":selected["requested_dataset"],"package_data_used":False,
                "input_selection_sha256":selected["manifest_sha256"],"platform":run_meta.get("platform"),
                "flowcell_chemistry":run_meta.get("flowcell_chemistry"),"basecaller":run_meta.get("basecaller"),
                "basecaller_model":run_meta.get("basecaller_model"),"molecule_type":run_meta.get("molecule_type"),
                "reference_build":run_meta.get("reference_build"),"callers":["sniffles2",second_name],"second_caller_mode":args.second_caller,"tool_versions":versions,
                "bam_sq_reference":identity["bam_sq_reference"],
                "upstream_identity_path":str(pathlib.Path(args.identity_check).resolve()),
                "upstream_identity_sha256":svlib.file_sha256(args.identity_check),
                "upstream_input_manifest_sha256":upstream_identity["input_manifest_sha256"],
                "coverage_scaled_support":support,
                "effective_min_support":{"sniffles2":support["sniffles2_min_support"],
                                         second_name:(support["cutesv_min_support"]
                                                      if args.second_caller=="cutesv" else None)}}
    (sv/"provenance.json").write_text(json.dumps(provenance,indent=2))
    manifest=[{"step":2,"file":_fig_counts(counts,second_counts,second_name,figures),"caption":"Normalized calls by class for both selected callers.","interpretation":"The class counts expose caller-specific composition after normalization. Differences remain sensitive to support thresholds, FILTER handling, and representation and do not identify the more accurate caller."},
              {"step":3,"file":_fig_sizes(calls+second_calls,figures),"caption":"Normalized non-breakend SV length spectrum across both callers at the >=50 bp calling scope.","interpretation":"The spectrum shows the sizes represented among retained non-breakend calls. Breakends and complex rearrangements are excluded, and caller representation can shift the apparent distribution."},
              {"step":4,"file":_fig_concordance(agreement["by_class"],second_name,figures),"caption":"Class-aware caller concordance; agreement is not accuracy.","interpretation":"Shared and caller-unique counts summarize descriptive robustness under the recorded matching rule. Concordance does not establish correctness without independent truth or orthogonal validation."}]
    for item in manifest: item["file"]=pathlib.Path(item["file"]).relative_to(out).as_posix()
    (figures/"manifest.json").write_text(json.dumps(manifest,indent=2))
    facts={"execution_mode":"analysis","requested_dataset":selected["requested_dataset"],"package_data_used":False,
           "input_selection_sha256":selected["manifest_sha256"],"sniffles2_sv_count":len(calls),"second_caller":second_name,"second_caller_sv_count":len(second_calls),"shared_sv_count":agreement["totals"]["shared"],"sniffles2_unique_count":agreement["totals"]["a_unique"],"second_caller_unique_count":agreement["totals"]["b_unique"],
           "sv_counts_by_class":{"sniffles2":counts,second_name:second_counts},"vcf_valid_indexed":bool(valid),"reference_build_mismatch_violations":0,
           "evidence_tier":args.evidence_tier,"reference_build":run_meta.get("reference_build"),"platform":run_meta.get("platform"),
           "platform_normalized":upstream_identity["platform_normalized"],
           "upstream_identity_sha256":svlib.file_sha256(args.identity_check),
           "coverage_estimate_x":support["coverage_estimate_x"],
           "sniffles2_min_support":support["sniffles2_min_support"],
           "second_caller_min_support":(support["cutesv_min_support"] if args.second_caller=="cutesv" else None),
           "bam_reference_identity_passed":True,"normalization_indexing_passed":bool(valid),
           "genotype_quality_table":"sv/sv_calls.tsv",
           "headline_sentence":f"Sniffles2 called {len(calls)} and {second_name} called {len(second_calls)} structural variants; {agreement['totals']['shared']} were concordant. Concordance is not accuracy."}
    (sv/"facts_payload.json").write_text(json.dumps(facts,indent=2))
    infographic_claims,infographic_prompt=svlib.facts_derived_infographic(facts)
    (sv/"infographic_claims.json").write_text(json.dumps(infographic_claims,indent=2))
    (sv/"infographic_prompt.txt").write_text(infographic_prompt)
    for source,stem in ((normalized,"calls_sniffles2.norm.vcf.gz"),(second_normalized,"calls_second.norm.vcf.gz")):
        for suffix in ("",".tbi"):
            if os.path.exists(source+suffix): shutil.copy(source+suffix,sv/(stem+suffix))
    science_contract.write_receipt(out,input_selection_sha256=selected["manifest_sha256"])
    shutil.rmtree(work,ignore_errors=True); print("run_sv complete:",json.dumps({"sniffles2":len(calls),"second_caller":len(second_calls),"shared":agreement["totals"]["shared"],"valid_indexed":valid})); return 0
if __name__=="__main__": raise SystemExit(main())
