#!/usr/bin/env python3
"""Seal long-read-structural-variants inputs under analysis-only schema v2."""
from __future__ import annotations
import argparse, hashlib, json, os, pathlib

SCHEMA_VERSION = "2.0"; PACKAGE_SLUG = "long-read-structural-variants"; ORIGINS = {"uploaded", "downloaded"}
class SelectionError(ValueError): pass
def file_sha256(path):
    digest=hashlib.sha256()
    with pathlib.Path(path).open("rb") as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b""): digest.update(chunk)
    return digest.hexdigest()
def canonical_manifest_sha256(value):
    unsigned={key:item for key,item in value.items() if key!="manifest_sha256"}
    raw=json.dumps(unsigned,sort_keys=True,separators=(",",":"),ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
def _inside(path,root):
    try: path.relative_to(root); return True
    except ValueError: return False
def _acquisition(origin,token):
    if origin=="uploaded":
        if token!="-": raise SelectionError("uploaded input acquisition token must be -")
        return {"status":"not_applicable"}
    if token=="-": raise SelectionError("downloaded input requires an acquisition JSON")
    value=json.loads(pathlib.Path(token).expanduser().resolve(strict=True).read_text())
    required={"status","source","tool","tool_version","command_or_api","retrieved_at"}
    if set(value)!=required or value.get("status")!="complete" or any(not str(value[key]).strip() for key in required-{"status"}):
        raise SelectionError("downloaded acquisition must be complete with all schema-v2 fields")
    return value
def seal(*,requested_dataset,inputs,result_root,selection_evidence,package_root):
    if not requested_dataset.strip() or not selection_evidence.strip(): raise SelectionError("requested_dataset and selection_evidence are required")
    package_root=pathlib.Path(package_root).resolve(strict=True); records=[]; roles=set()
    for item in inputs:
        role=str(item["role"]).strip(); origin=item["origin"]; source=str(item["requested_source"]).strip()
        if not role or role in roles or origin not in ORIGINS or not source: raise SelectionError("each input needs a unique role, origin, and requested source")
        roles.add(role); requested=pathlib.Path(item["path"]).expanduser(); lexical=requested.absolute(); resolved=requested.resolve(strict=True)
        if not resolved.is_file(): raise SelectionError(f"input is not a regular file: {resolved}")
        forbidden={"demo","eval","fixture","fixtures","validation_bundle"}
        if _inside(lexical,package_root) or _inside(resolved,package_root) or forbidden.intersection(part.lower() for part in resolved.parts):
            raise SelectionError(f"analysis rejects package or validation-data input: {role}")
        records.append({"role":role,"origin":origin,"requested_source":source,"resolved_path":str(resolved),
                        "bytes":resolved.stat().st_size,"sha256":file_sha256(resolved),
                        "acquisition":_acquisition(origin,item.get("acquisition_token","-"))})
    root=pathlib.Path(result_root).expanduser().resolve(strict=False)
    if _inside(root,package_root): raise SelectionError("result_root must be outside the package")
    if any(_inside(pathlib.Path(item["resolved_path"]),root) for item in records): raise SelectionError("result_root cannot contain a selected input")
    if root.exists() and any(root.iterdir()): raise SelectionError("result_root must be empty before sealing")
    root.mkdir(parents=True,exist_ok=True)
    manifest={"schema_version":SCHEMA_VERSION,"execution_mode":"analysis","requested_dataset":requested_dataset.strip(),
              "inputs":records,"package_data_used":False,"result_root":str(root),"selection_evidence":selection_evidence.strip()}
    manifest["manifest_sha256"]=canonical_manifest_sha256(manifest); path=root/"input_selection.json"
    with path.open("x",encoding="utf-8") as handle: handle.write(json.dumps(manifest,indent=2,sort_keys=True)+"\n")
    os.chmod(path,0o444); return path,manifest
def verify(path):
    value=json.loads(pathlib.Path(path).read_text())
    required={"schema_version","execution_mode","requested_dataset","inputs","package_data_used","result_root","selection_evidence","manifest_sha256"}
    if set(value)!=required or value.get("schema_version")!="2.0" or value.get("execution_mode")!="analysis" or value.get("package_data_used") is not False:
        raise SelectionError("selection does not match analysis-only schema v2")
    if value.get("manifest_sha256")!=canonical_manifest_sha256(value): raise SelectionError("selection self-hash mismatch")
    for item in value["inputs"]:
        if file_sha256(pathlib.Path(item["resolved_path"]).resolve(strict=True))!=item["sha256"]: raise SelectionError("selected input changed after sealing")
        if item["origin"]=="uploaded" and item["acquisition"]!={"status":"not_applicable"}: raise SelectionError("uploaded acquisition is invalid")
        if item["origin"]=="downloaded":
            required_acq={"status","source","tool","tool_version","command_or_api","retrieved_at"}
            if set(item["acquisition"])!=required_acq or item["acquisition"].get("status")!="complete": raise SelectionError("downloaded acquisition is incomplete")
    return value
def assert_selected(manifest,roles_to_paths,allow_additional=False):
    value=verify(manifest); selected={item["role"]:pathlib.Path(item["resolved_path"]) for item in value["inputs"]}
    if (not allow_additional and set(selected)!=set(roles_to_paths)) or (allow_additional and not set(roles_to_paths).issubset(selected)): raise SelectionError("runtime input roles differ from sealed selection")
    for role,path in roles_to_paths.items():
        if pathlib.Path(path).expanduser().resolve(strict=True)!=selected[role]: raise SelectionError(f"runtime path differs from sealed selection: {role}")
    return value
def main():
    parser=argparse.ArgumentParser(); sub=parser.add_subparsers(dest="command",required=True)
    make=sub.add_parser("seal"); make.add_argument("--requested-dataset",required=True); make.add_argument("--selection-evidence",required=True)
    make.add_argument("--result-root",required=True); make.add_argument("--input",action="append",nargs=5,metavar=("ROLE","PATH","ORIGIN","REQUESTED_SOURCE","ACQUISITION"))
    check=sub.add_parser("verify"); check.add_argument("manifest"); args=parser.parse_args()
    if args.command=="verify": print(json.dumps(verify(args.manifest),indent=2)); return
    if not args.input: parser.error("at least one --input is required")
    items=[{"role":r,"path":p,"origin":o,"requested_source":s,"acquisition_token":a} for r,p,o,s,a in args.input]
    path,_=seal(requested_dataset=args.requested_dataset,inputs=items,result_root=args.result_root,
                selection_evidence=args.selection_evidence,package_root=pathlib.Path(__file__).resolve().parents[1])
    print(path)
if __name__=="__main__": main()
