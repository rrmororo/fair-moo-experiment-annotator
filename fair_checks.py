"""
fair_checks.py — check-based (formative) FAIR verification for curation.

Runs over the curator's inputs and the received files BEFORE deposit, by
principle (F/A/I/R) and severity (error/warning/info), with actionable
messages. A declaration of absence satisfies the curation procedure, but the
limitation keeps being reported here — completeness and conformance are
different things. The report goes into the RO-Crate as evidence.
"""

import csv
import json
import re
from pathlib import Path

import wikidata

_NUMERIC = re.compile(r"^-?\d+([.,]\d+)?([eE][+-]?\d+)?$")
_PROPRIETARY = {".xlsx", ".xls", ".mat", ".sav", ".dta", ".rds", ".accdb"}
_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def _result(check_id, principle, severity, ok, message):
    return {"id": check_id, "principle": principle, "severity": severity,
            "ok": ok, "message": message}


# ------------------------------------------------------------- tabulares ----
def _looks_like_header(row):
    cells = [c.strip() for c in row]
    if not cells:
        return False, "first row is empty"
    if all(_NUMERIC.match(c) for c in cells if c):
        return False, "all cells in the first row are numeric"
    if any(not c for c in cells):
        return False, "there are empty column names"
    if len(set(c.lower() for c in cells)) != len(cells):
        return False, "there are duplicated column names"
    return True, ""


def _sniff_delimiter(path: Path, text: str) -> str:
    """Detect the actual delimiter instead of assuming ','. Institutional and
    experiment CSVs in this project are ';'-delimited; a hardcoded comma made
    every free-text comma look like a column break (false T5 positives)."""
    if path.suffix.lower() == ".tsv":
        return "\t"
    first = text.splitlines()[0] if text else ""
    return ";" if first.count(";") > first.count(",") else ","


def check_tabular_file(path: Path, label: str | None = None) -> list[dict]:
    label = label or path.name
    results = []
    if path.suffix.lower() in _PROPRIETARY:
        results.append(_result(
            "T4-open-format", "I", "warning", False,
            f"'{label}': proprietary format ({path.suffix}). Prefer CSV/"
            "Parquet for interoperability (I1)."))
        return results
    if path.suffix.lower() not in {".csv", ".tsv"}:
        return results  # .ctt, .java, .pdf etc.: no tabular verification
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        results.append(_result(
            "T3-encoding", "I", "error", False,
            f"'{label}': is not readable UTF-8. Re-export as UTF-8 (I1)."))
        return results
    delim = _sniff_delimiter(path, text)
    rows = list(csv.reader(text.splitlines(), delimiter=delim))
    if not rows:
        results.append(_result("T0-empty", "R", "error", False,
                               f"'{label}': empty file."))
        return results
    has_header, why = _looks_like_header(rows[0])
    if has_header:
        results.append(_result(
            "T1-header", "R", "info", True,
            f"'{label}': header present ({len(rows[0])} columns)."))
    else:
        results.append(_result(
            "T1-header", "R", "error", False,
            f"'{label}': dataset apparently WITHOUT a header ({why}). Without "
            "column names, fields are neither interpretable nor mappable "
            "in Croissant — incompatible with I and R. Add a header or "
            "document the columns in the file description."))
    expected = len(rows[0])
    bad_rows = [i for i, r in enumerate(rows[1:], start=2)
                if r and len(r) != expected]
    if bad_rows:
        shown = bad_rows[:10]
        more = f" (+{len(bad_rows) - 10} more)" if len(bad_rows) > 10 else ""
        results.append(_result(
            "T5-irregular-rows", "R", "warning", False,
            f"'{label}': {len(bad_rows)} row(s) with a column count different "
            f"from the header ({expected} columns) — rows {shown}{more}. "
            f"Delimiter detected: '{delim}'."))
    return results


# ------------------------------------------------------------- metadados ----
def check_metadata(meta: dict) -> list[dict]:
    r = []
    nr = meta.get("not_recorded", {})

    r.append(_result("F1-pid", "F", "info", True,
                     "Object PID: DOI pre-reserved at deposit (registered "
                     "in DataCite only after publication)."))
    desc = meta.get("description") or ""
    r.append(_result(
        "F2-rich-description", "F", "warning" if len(desc) < 50 else "info",
        len(desc) >= 50,
        "Rich description (F2): OK." if len(desc) >= 50 else
        f"Description has {len(desc)} characters — short for F2. Describe the "
        "original experiment, the received material and the curation scope."))
    kw = meta.get("keywords") or []
    r.append(_result(
        "F4-keywords", "F", "warning" if len(kw) < 3 else "info", len(kw) >= 3,
        "Keywords: OK." if len(kw) >= 3 else
        f"Only {len(kw)} keyword(s) — ≥3 recommended (F4)."))

    r.append(_result(
        "A1-access", "A", "error" if not meta.get("access_right") else "info",
        bool(meta.get("access_right")),
        f"access_right: {meta.get('access_right') or 'NOT DEFINED (A1)'}."))
    if str(meta.get("authorization_status", "")).startswith("no authorization") \
            and meta.get("received_files"):
        r.append(_result(
            "A2-metadata-only", "A", "info", True,
            "No redistribution authorization: the received files will NOT "
            "be deposited (metadata-only). Metadata persist even "
            "without access to the data (A2). The deposit identifies the resource and the "
            "access conditions — it is not equivalent to publishing the original dataset."))

    # I — semantic anchors and invalid QIDs
    all_rows = (meta.get("algorithms", []) + meta.get("indicators", []) +
                meta.get("concept_qids", []))
    invalid = [row.get("qid") for row in all_rows
               if row.get("qid") and not wikidata.normalize_qid(row.get("qid"))]
    valid = [row for row in all_rows if wikidata.normalize_qid(row.get("qid"))]
    if invalid:
        r.append(_result("I2-invalid-qid", "I", "error", False,
                         f"Invalid QIDs (they will be ignored): {invalid}."))
    r.append(_result(
        "I1-semantic-anchors", "I", "warning" if not valid else "info",
        bool(valid),
        f"{len(valid)} concept(s) anchored on Wikidata." if valid else
        "No algorithm/indicator/concept anchored on Wikidata (I1): use the "
        "sidebar search, especially for the algorithms."))

    # R — license, provenance, hypervolume conditional, evidence
    r.append(_result(
        "R1.1-license", "R", "error" if not meta.get("license") else "info",
        bool(meta.get("license")),
        f"License: {meta.get('license') or 'MISSING (R1.1)'}."))
    r.append(_result(
        "R1.2-source", "R",
        "error" if not meta.get("source_document") else "info",
        bool(meta.get("source_document")),
        "Source document declared (provenance of the metadata)."
        if meta.get("source_document") else
        "Source document MISSING — mandatory in curation (R1.2)."))

    # C: reported hypervolume requires a reference point (or declared absence)
    for i, ind in enumerate(meta.get("indicators", [])):
        name = (ind.get("name") or "").lower()
        if "hypervolume" in name or name in ("hv", "whv"):
            params = (ind.get("params") or "").strip()
            declared = bool(nr.get("indicators", "").strip())
            ok = bool(params) or declared
            r.append(_result(
                "C-hv-reference-point", "R",
                "error" if not ok else "info", ok,
                "Hypervolume with documented parameters." if params else
                ("Hypervolume: parameters not recorded in the source — absence "
                 "declarada." if declared else
                 "Hypervolume reported WITHOUT reference point/normalisation (C): "
                 "document the parameters or declare 'not recorded in the "
                 "source' — without them the values are not interpretable.")))

    # evidence: filled fields must carry evidence or a declared absence
    evidence = meta.get("evidence", {})
    skip = {"curators", "curation_notes", "access_right", "communities",
            "authorization_status", "source_document", "license", "version",
            "language", "keywords", "title"}
    uncovered = [k for k, v in meta.items()
                 if k not in skip and isinstance(v, str) and v.strip()
                 and k not in evidence and k not in nr
                 and k not in {"doi", "confirmed_at", "description"}]
    r.append(_result(
        "R1.2-evidence", "R", "warning" if uncovered else "info",
        not uncovered,
        "All extracted fields have evidence or a declared absence."
        if not uncovered else
        f"Fields with neither documentary evidence nor a declared absence: "
        f"{uncovered}. Each extracted value must indicate where it came from in the source."))
    return r


def check_pids(meta: dict) -> list[dict]:
    results = []
    pid_results = meta.get("pid_results")
    if pid_results is None:
        results.append(_result(
            "PID-verification", "R", "warning", False,
            "PIDs not verified yet: run 'Verify PIDs' (ORCID/ROR/"
            "Crossref) and confirm each identifier."))
        return results
    confirmed = meta.get("pid_confirmations", {})
    for p in pid_results:
        key = p.get("normalized") or p["raw"]
        if not p.get("normalized"):
            results.append(_result(
                "PID-format", "F", "error", False,
                f"{p['kind'].upper()} '{p['raw']}' ({p['where']}): invalid format."))
        elif not confirmed.get(key):
            results.append(_result(
                "PID-unconfirmed", "R", "warning", False,
                f"{p['kind'].upper()} {key} ({p['where']}): resolved as "
                f"'{p.get('label') or p.get('error')}' but NOT confirmed."))
        else:
            results.append(_result(
                "PID-confirmed", "R", "info", True,
                f"{p['kind'].upper()} {key} confirmed → {p.get('label')}."))
    return results


def check_disclosure_control(meta: dict) -> list[dict]:
    """Ethics/RGPD gate for the institutional dataset. Enforces the ethics
    opinion: authorisation + documented anonymisation, never open microdata,
    and (in synthetic mode) a generated synthetic dataset that reproduces no
    real record. Runs before deposit; blocks the irreversible mistake."""
    r = []
    mode = (meta.get("sharing_mode") or "").strip().lower()
    mode = mode.split("—")[0].split("-")[0].strip()  # 'synthetic', 'restricted', ...
    rf = meta.get("received_files", [])

    def _is_inst(f):
        name = str(f.get("name", "")).lower().replace(" (raw, restricted — files under controlled access)", "")
        return name.endswith("horarios.csv")

    distributed_micro = [f for f in rf if _is_inst(f)
                         and not f.get("metadata-only — files not distributedy")
                         and not f.get("synthetic — open synthetic stand-in, raw restricted") and not f.get("aggregated — only aggregated/derived data published")]

    r.append(_result(
        "DP1-authorization", "R",
        "error" if not meta.get("institutional_authorization") else "info",
        bool(meta.get("institutional_authorization")),
        "Institutional authorization recorded."
        if meta.get("institutional_authorization") else
        "Institutional authorization for use AND deposit is MISSING (ethics "
        "opinion, point 1)."))
    r.append(_result(
        "DP2-anon-doc", "R",
        "error" if not meta.get("anonymization_doc") else "info",
        bool(meta.get("anonymization_doc")),
        "Disclosure-control documentation present." if meta.get("anonymization_doc")
        else "Anonymization/disclosure-control documentation MISSING: describe the "
             "original data, the fields treated, and data used vs data published."))
    if distributed_micro:
        r.append(_result(
            "DP3-raw-open", "A", "error", False,
            f"Institutional microdata would be distributed openly "
            f"({[f['name'] for f in distributed_micro]}). Pseudonymisation is not "
            "sufficient — publish a synthetic/aggregated version or restrict access."
            + ("  NOTE: sharing mode is already "
               f"'{mode}' — the raw inventory entry itself is missing "
               "metadata_only/restricted. Annotation generation must mark it; "
               "re-run generation and do NOT re-ingest afterwards."
               if mode in ("aggregated", "synthetic") else "")))

    if mode == "synthetic":
        syn = meta.get("synthesis")
        r.append(_result(
            "DP4-synthetic", "R", "error" if not syn else "info", bool(syn),
            "Synthetic dataset generated (with report)." if syn else
            "Sharing mode is 'synthetic' but no synthetic dataset was generated — "
            "run annotation generation before depositing."))
        if syn:
            leaked = syn.get("real_rows_reproduced", 0)
            r.append(_result(
                "DP5-no-leak", "A", "error" if leaked else "info", not leaked,
                "No synthetic row reproduces a real record." if not leaked else
                f"{leaked} synthetic row(s) coincide with real records — regenerate "
                "with a higher rare-category threshold."))
        raw_restricted = any(f.get("metadata_only")
                             and "raw, restricted" in str(f.get("name", "")) for f in rf)
        r.append(_result(
            "DP6-raw-restricted", "A",
            "warning" if not raw_restricted else "info", raw_restricted,
            "Raw institutional dataset kept restricted." if raw_restricted else
            "Raw institutional dataset is not marked restricted — verify it is withheld."))

    if mode == "aggregated":
        agg = meta.get("aggregation")
        r.append(_result(
            "DP4-aggregated", "R", "error" if not agg else "info", bool(agg),
            "Aggregates generated (with report)." if agg else
            "Sharing mode is 'aggregated' but no aggregates were generated — "
            "run annotation generation before depositing."))
        if agg:
            below = agg.get("published_cells_below_k", 0)
            r.append(_result(
                "DP5-small-cell", "A", "error" if below else "info", not below,
                "No published cell is below the small-cell threshold." if not below
                else f"{below} published cell(s) below k — regenerate with "
                     "suppression."))
        raw_restricted = any(f.get("metadata_only")
                             and "raw, restricted" in str(f.get("name", "")) for f in rf)
        r.append(_result(
            "DP6-raw-restricted", "A",
            "warning" if not raw_restricted else "info", raw_restricted,
            "Raw institutional dataset kept restricted." if raw_restricted else
            "Raw institutional dataset is not marked restricted — verify it is withheld."))

    dpo = (meta.get("dpo_consultation") or "").lower()
    r.append(_result(
        "DP7-dpo", "R", "warning" if dpo != "completed" else "info", dpo == "completed",
        "DPO consultation completed." if dpo == "completed" else
        f"DPO consultation status: {meta.get('dpo_consultation') or 'not recorded'} — "
        "recommended before publishing."))
    return r


def check_all(meta: dict) -> list[dict]:
    results = check_metadata(meta)
    results.extend(check_pids(meta))
    results.extend(check_disclosure_control(meta))
    for f in meta.get("received_files", []):
        p = Path(f["path"])
        if p.is_file():
            results.extend(check_tabular_file(p, label=f["name"]))
    results.sort(key=lambda x: (_SEVERITY_ORDER[x["severity"]], x["principle"]))
    return results


def summary(results):
    return {"errors": sum(1 for r in results if r["severity"] == "error" and not r["ok"]),
            "warnings": sum(1 for r in results if r["severity"] == "warning" and not r["ok"]),
            "ok": sum(1 for r in results if r["ok"])}


def write_report(results, out_dir: Path) -> Path:
    out_dir.mkdir(exist_ok=True)
    path = out_dir / "fair_check_report.json"
    path.write_text(json.dumps({"summary": summary(results), "results": results},
                               ensure_ascii=False, indent=2), encoding="utf-8")
    return path
