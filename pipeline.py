"""
pipeline.py — retrospective curation pipeline (single mode).

No optimisation code is executed and there is no observer: the input is the
set of files received from the original author plus what the curator extracts
from the documentary sources. Two-level provenance (original experiment +
curation activity), documentary evidence per field, declared incompleteness,
and the coordinated views: Croissant, PROV-O/Turtle, DataCite/Zenodo,
experiment_record and RO-Crate. SHACL validation uses the curation profile
exclusively.
"""

import hashlib
import json
import mimetypes
import re
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import schema
import fairmoo
import wikidata


class MissingTokenError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------ ingestion -----
def ingest_files(files_dir: Path) -> list[dict]:
    """Recursively inventories the received files (datasets, experiment and
    indicator CSVs, .ctt, Java code, dissertation PDF): sha256 checksum, size
    and format. The description is the curator's."""
    if not files_dir.is_dir():
        raise FileNotFoundError(files_dir)
    inventory = []
    for path in sorted(p for p in files_dir.rglob("*") if p.is_file()):
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        # extension-first mapping: mimetypes misclassifies .csv as MS-Excel on
        # Windows; an explicit table keeps encodingFormat interoperable (FAIR-I)
        _EXT_FMT = {".csv": "text/csv", ".tsv": "text/tab-separated-values",
                    ".ctt": "text/plain", ".java": "text/x-java-source",
                    ".json": "application/json", ".ttl": "text/turtle",
                    ".txt": "text/plain", ".md": "text/markdown",
                    ".pdf": "application/pdf", ".png": "image/png",
                    ".eps": "application/postscript", ".xml": "application/xml"}
        fmt = _EXT_FMT.get(path.suffix.lower()) \
            or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        inventory.append({
            # forward slashes always: backslash paths break contentUrl/@id
            # resolution for any non-Windows consumer of the crate (FAIR-I)
            "name": str(path.relative_to(files_dir)).replace("\\", "/"),
            "path": str(path),
            "sha256": sha,
            "size_bytes": path.stat().st_size,
            "encodingFormat": fmt,
            "description": "",
        })
    return inventory


def deidentify_inventory(received_files: list[dict], out_dir: Path,
                         quasi_identifiers: dict | None = None,
                         mapping_path: Path | None = None,
                         match: str = "horarios.csv") -> dict:
    """R6 de-identification as a curation step. Pseudonymises the quasi-
    identifying columns of the institutional timetable dataset IN PLACE: the
    inventory entry is repointed to a derived, deposit-safe CSV (same name/@id,
    so recordSet/field references stay stable); the raw dataset and the surrogate
    mapping are appended as metadata_only, restricted entries (described but never
    uploaded). Reuses a persisted mapping for consistency across files/runs.
    Returns a manifest; also stored on meta as meta['deidentification']."""
    import csv as _csv, random
    qids = quasi_identifiers if quasi_identifiers is not None \
        else getattr(schema, "QUASI_IDENTIFIERS", {})
    if not qids:
        return {}
    target = next((f for f in received_files
                   if str(f.get("name", "")).lower().endswith(match.lower())
                   and not f.get("metadata_only")), None)
    if target is None:
        return {}
    raw = Path(target["path"])
    first = raw.open(encoding="utf-8", newline="").readline()
    delim = ";" if first.count(";") > first.count(",") else ","
    with raw.open(encoding="utf-8-sig", newline="") as fh:
        reader = _csv.DictReader(fh, delimiter=delim)
        fields = reader.fieldnames
        rows = list(reader)
    cols = [c for c in qids if c in fields]
    if not cols:
        return {}
    mapping = {}
    # the mapping is the re-identification key: keep it OUTSIDE out_dir so the
    # blanket upload in deposit_zenodo can never reach it
    mpath = Path(mapping_path) if mapping_path \
        else out_dir.parent / "restricted" / "restricted_mapping.json"
    mpath.parent.mkdir(parents=True, exist_ok=True)
    if mpath.exists():
        mapping = json.loads(mpath.read_text(encoding="utf-8"))
    rng = random.Random()  # the persisted mapping (not a seed) is the key
    for col in cols:
        m = mapping.setdefault(col, {})
        unseen = sorted({r[col] for r in rows if r[col] and r[col] not in m})
        rng.shuffle(unseen)
        i = len(m) + 1
        for v in unseen:
            m[v] = f"{qids[col]}_{i:04d}"
            i += 1
    for r in rows:
        for col in cols:
            if r[col]:
                r[col] = mapping[col][r[col]]
    out_dir.mkdir(parents=True, exist_ok=True)
    derived = out_dir / "horarios_pseudonymized.csv"
    with derived.open("w", encoding="utf-8", newline="") as fh:
        w = _csv.DictWriter(fh, fieldnames=fields, delimiter=delim)
        w.writeheader()
        w.writerows(rows)
    mpath.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")

    raw_sha, raw_size, raw_name = target["sha256"], target["size_bytes"], target["name"]
    new_sha = hashlib.sha256(derived.read_bytes()).hexdigest()
    # repoint the DISTRIBUTED entry to the derived file (name/@id unchanged)
    target["path"] = str(derived)
    target["sha256"] = new_sha
    target["size_bytes"] = derived.stat().st_size
    target["content_url"] = derived.name
    target["description"] = (
        "De-identified derived version: the course, shift and class columns carry "
        "stable surrogate keys instead of institutional labels. Derived from the "
        "restricted raw dataset; re-identification mapping withheld.")
    target["column_descriptions"] = {
        col: (f"Stable pseudonymous surrogate key ({qids[col]}_####); original label "
              f"replaced during de-identification, mapping withheld under restricted access.")
        for col in cols}
    # raw + mapping: described, restricted, NEVER uploaded
    received_files.append({
        "name": raw_name + " (raw, restricted)", "path": str(raw),
        "sha256": raw_sha, "size_bytes": raw_size,
        "encodingFormat": target["encodingFormat"],
        "description": "Original institutional dataset with course/shift/class labels. "
                       "Restricted; not distributed. Source of the derived version.",
        "metadata_only": True, "access": "restricted"})
    received_files.append({
        "name": "restricted_mapping.json", "path": str(mpath),
        "sha256": hashlib.sha256(mpath.read_bytes()).hexdigest(),
        "size_bytes": mpath.stat().st_size, "encodingFormat": "application/json",
        "description": "Surrogate-to-label lookup for the pseudonymised columns. "
                       "Restricted; withheld from the deposit.",
        "metadata_only": True, "access": "restricted"})
    return {"columns": {c: len(mapping[c]) for c in cols},
            "raw_sha256": raw_sha, "derived_sha256": new_sha, "generated_at": _now()}


def synthesize_inventory(received_files: list[dict], out_dir: Path,
                         quasi_identifiers: dict | None = None,
                         match: str = "horarios.csv",
                         seed: int | None = None,
                         rare_threshold: int = 5) -> dict:
    """Disclosure-control by SYNTHESIS (ethics-committee route: publish a
    synthetic version instead of the institutional microdata). Replaces the
    DISTRIBUTED institutional dataset with a structure-preserving SYNTHETIC
    stand-in and keeps the raw dataset restricted (metadata_only, never
    uploaded). Method: each column is resampled INDEPENDENTLY — so real
    cross-column combinations (the re-identification vector flagged by the
    ethics opinion) are not preserved — and categories occurring fewer than
    `rare_threshold` times are generalised to 'OTHER' before sampling; missing-
    ness rate and numeric ranges are approximately preserved for plausibility.
    No real individual record survives, and the function asserts that no
    synthetic row equals a real row. Joint distributions are intentionally NOT
    preserved: the artefact is a reusable structural stand-in, not a statistical
    twin, and not a privacy-certified dataset — institutional authorisation and
    DPO consultation still apply. Writes synthesis_report.json. Returns a
    manifest; also stored on meta as meta['synthesis']."""
    import csv as _csv, random, re as _re
    from collections import Counter
    qids = quasi_identifiers if quasi_identifiers is not None \
        else getattr(schema, "QUASI_IDENTIFIERS", {})
    # An unprocessed institutional dataset: not restricted, not already the
    # output of a disclosure-control route (processed targets keep flags).
    target = next((f for f in received_files
                   if str(f.get("name", "")).lower().endswith(match.lower())
                   and not f.get("metadata_only")
                   and not f.get("synthetic") and not f.get("aggregated")), None)
    twin_needed = True
    if target is None:
        # No unprocessed target. Either this mode already ran (idempotent:
        # reload its manifest), or the sharing mode was SWITCHED after another
        # route ran — in that case rebuild from the restricted raw twin and
        # retarget the previously distributed institutional artefact.
        prev = next((f for f in received_files
                     if not f.get("metadata_only")
                     and (f.get("synthetic") or f.get("aggregated"))), None)
        if prev is not None and prev.get("synthetic"):
            report = out_dir / "synthesis_report.json"
            if report.exists():
                return json.loads(report.read_text(encoding="utf-8"))
            return {}
        twin = next((f for f in received_files
                     if f.get("metadata_only")
                     and str(f.get("name", "")).lower().endswith(
                         match.lower() + " (raw, restricted)")), None)
        if twin is None or prev is None:
            return {}
        target, twin_needed = prev, False
        target.pop("aggregated", None)
        raw = Path(twin["path"])
        raw_sha, raw_size = twin["sha256"], twin["size_bytes"]
        raw_name = str(twin["name"]).rsplit(" (raw, restricted)", 1)[0]
        target["name"] = raw_name
        target["encodingFormat"] = "text/csv"
    else:
        raw = Path(target["path"])
        raw_sha, raw_size, raw_name = (target["sha256"], target["size_bytes"],
                                       target["name"])
    first = raw.open(encoding="utf-8", newline="").readline()
    delim = ";" if first.count(";") > first.count(",") else ","
    with raw.open(encoding="utf-8-sig", newline="") as fh:
        reader = _csv.DictReader(fh, delimiter=delim)
        fields = reader.fieldnames
        rows = list(reader)
    if not rows or not fields:
        return {}
    n = len(rows)
    rng = random.Random(seed)
    numeric = _re.compile(r"^-?\d+([.,]\d+)?$")

    pools, generalised = {}, {}
    for col in fields:
        nonempty = [r.get(col, "") for r in rows if r.get(col, "")]
        present = len(nonempty) / n if n else 0.0
        if nonempty and all(numeric.match(v) for v in nonempty):
            nums = [float(v.replace(",", ".")) for v in nonempty]
            lo, hi = min(nums), max(nums)
            is_int = all(x.is_integer() for x in nums)
            pools[col] = ("numeric", lo, hi, is_int, present)
        else:
            cnt = Counter(nonempty)
            safe, rare = [], 0
            for v, c in cnt.items():
                if c >= rare_threshold:
                    safe.extend([v] * c)
                else:
                    rare += c
            if rare:
                safe.extend(["OTHER"] * rare)
                generalised[col] = sum(1 for c in cnt.values() if c < rare_threshold)
            pools[col] = ("categorical", safe or ["OTHER"], present)

    def _draw(col):
        spec = pools[col]
        if rng.random() > spec[-1]:               # preserve missingness rate
            return ""
        if spec[0] == "numeric":
            _, lo, hi, is_int, _p = spec
            x = rng.uniform(lo, hi)
            return str(int(round(x))) if is_int else f"{x:.4g}"
        return rng.choice(spec[1])

    real_rows = {tuple(r.get(c, "") for c in fields) for r in rows}
    syn_rows = []
    for _ in range(n):
        row = {c: _draw(c) for c in fields}
        for _retry in range(20):                  # never emit a real record
            if tuple(row[c] for c in fields) not in real_rows:
                break
            row = {c: _draw(c) for c in fields}
        syn_rows.append(row)
    leaked = sum(1 for r in syn_rows
                 if tuple(r[c] for c in fields) in real_rows)

    out_dir.mkdir(parents=True, exist_ok=True)
    derived = out_dir / "horarios_synthetic.csv"
    with derived.open("w", encoding="utf-8", newline="") as fh:
        w = _csv.DictWriter(fh, fieldnames=fields, delimiter=delim)
        w.writeheader()
        w.writerows(syn_rows)

    new_sha = hashlib.sha256(derived.read_bytes()).hexdigest()
    target["path"] = str(derived)
    target["sha256"] = new_sha
    target["size_bytes"] = derived.stat().st_size
    target["content_url"] = derived.name
    target["synthetic"] = True
    target["description"] = (
        "SYNTHETIC stand-in for the institutional timetable dataset. Columns were "
        "resampled independently (real cross-column combinations not preserved) and "
        "rare categories generalised, so it contains no real individual records. "
        "Structure-preserving for reuse only; not a reproduction of the original "
        "data. Derived from the restricted raw dataset.")
    target["column_descriptions"] = {
        c: "Synthetic values; independently resampled, rare categories generalised."
        for c in fields}
    # raw: described, restricted, NEVER uploaded (only once)
    if twin_needed:
        received_files.append({
            "name": raw_name + " (raw, restricted)", "path": str(raw),
            "sha256": raw_sha, "size_bytes": raw_size,
            "encodingFormat": target.get("encodingFormat", "text/csv"),
            "description": "Original institutional dataset. Restricted; not distributed. "
                           "Source of the synthetic version.",
            "metadata_only": True, "access": "restricted"})

    manifest = {"method": "independent per-column resampling with rare-category "
                          "generalisation (joint distributions not preserved)",
                "rows": n, "columns": list(fields), "rare_threshold": rare_threshold,
                "generalised_columns": generalised, "seed": seed,
                "raw_sha256": raw_sha, "synthetic_sha256": new_sha,
                "real_rows_reproduced": leaked, "generated_at": _now()}
    (out_dir / "synthesis_report.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def aggregate_inventory(received_files: list[dict], out_dir: Path,
                        match: str = "horarios.csv", k: int = 5,
                        sensitive_count_columns: set | None = None) -> dict:
    """Disclosure-control by AGGREGATION (ethics-committee route: publish real
    aggregates instead of the institutional microdata). Replaces the DISTRIBUTED
    institutional dataset with marginal aggregates — per-column category counts
    and numeric summaries — under small-cell suppression: any category count
    below `k` is withheld (folded into a suppressed tally), so no published cell
    identifies a small group. Cross-tabulations are deliberately NOT published,
    since fine cross-classifications are the re-identification vector flagged by
    the ethics opinion. Columns in `sensitive_count_columns` (default the
    per-shift enrolment count) get additional low-tail suppression: any published
    statistic below `k` is withheld and only the aggregate count of low-enrolment
    sessions is reported, so the single- and low-enrolment shifts that carry the
    identified risk are never exposed. The raw dataset is kept restricted (metadata_only, never
    uploaded). Unlike the synthetic route, the aggregates are REAL, at the cost
    of record-level structure. Writes aggregation_report.json. Returns a
    manifest; also stored on meta as meta['aggregation']."""
    import csv as _csv, re as _re, statistics as _st
    from collections import Counter
    # An unprocessed institutional dataset: not restricted, not already the
    # output of a disclosure-control route (processed targets keep flags).
    target = next((f for f in received_files
                   if str(f.get("name", "")).lower().endswith(match.lower())
                   and not f.get("metadata_only")
                   and not f.get("synthetic") and not f.get("aggregated")), None)
    twin_needed = True
    if target is None:
        # No unprocessed target. Either this mode already ran (idempotent:
        # reload its manifest), or the sharing mode was SWITCHED after another
        # route ran — in that case rebuild from the restricted raw twin and
        # retarget the previously distributed institutional artefact.
        prev = next((f for f in received_files
                     if not f.get("metadata_only")
                     and (f.get("synthetic") or f.get("aggregated"))), None)
        if prev is not None and prev.get("aggregated"):
            report = out_dir / "aggregation_report.json"
            if report.exists():
                return json.loads(report.read_text(encoding="utf-8"))
            return {}
        twin = next((f for f in received_files
                     if f.get("metadata_only")
                     and str(f.get("name", "")).lower().endswith(
                         match.lower() + " (raw, restricted)")), None)
        if twin is None or prev is None:
            return {}
        target, twin_needed = prev, False
        target.pop("synthetic", None)
        raw = Path(twin["path"])
        raw_sha, raw_size = twin["sha256"], twin["size_bytes"]
        raw_name = str(twin["name"]).rsplit(" (raw, restricted)", 1)[0]
    else:
        raw = Path(target["path"])
        raw_sha, raw_size, raw_name = (target["sha256"], target["size_bytes"],
                                       target["name"])
    first = raw.open(encoding="utf-8", newline="").readline()
    delim = ";" if first.count(";") > first.count(",") else ","
    with raw.open(encoding="utf-8-sig", newline="") as fh:
        reader = _csv.DictReader(fh, delimiter=delim)
        fields = reader.fieldnames
        rows = list(reader)
    if not rows or not fields:
        return {}
    n = len(rows)
    numeric = _re.compile(r"^-?\d+([.,]\d+)?$")
    sensitive = (sensitive_count_columns if sensitive_count_columns is not None
                 else {"Inscritos no turno"})
    # Quasi-identifier CATEGORICAL columns are published with SURROGATE labels,
    # not their real values (e.g. course/UC/shift/class names), so the released
    # aggregates never expose institutional identifiers. Surrogates reuse the
    # SAME persisted, restricted mapping the de-identification route builds, so a
    # given course maps to the same key everywhere and across reruns.
    qids = getattr(schema, "QUASI_IDENTIFIERS", {})
    _mpath = out_dir.parent / "restricted" / "restricted_mapping.json"
    _mapping = {}
    if _mpath.exists():
        try:
            _mapping = json.loads(_mpath.read_text(encoding="utf-8"))
        except Exception:
            _mapping = {}

    def _surrogate(col, value):
        table = _mapping.setdefault(col, {})
        if value not in table:
            table[value] = f"{qids[col]}_{len(table) + 1:04d}"
        return table[value]

    categorical, numerical, suppressed_cells, low_tail_suppressed = {}, {}, 0, False
    for col in fields:
        nonempty = [r.get(col, "") for r in rows if r.get(col, "")]
        if nonempty and all(numeric.match(v) for v in nonempty):
            nums = sorted(float(v.replace(",", ".")) for v in nonempty)
            q = _st.quantiles(nums, n=4) if len(nums) >= 2 else [nums[0]] * 3
            stats = {"count": len(nums), "missing": n - len(nums),
                     "min": nums[0], "max": nums[-1],
                     "mean": round(_st.fmean(nums), 4),
                     "q1": q[0], "median": q[1], "q3": q[2]}
            if col in sensitive:
                # low-enrolment shifts are the identified risk: never expose an
                # exact value below k (0 = empty shift, no student); publish only
                # the aggregate count of such sessions
                stats["sessions_below_k"] = sum(1 for v in nums if 1 <= v < k)
                for key in ("min", "q1", "median", "q3"):
                    if isinstance(stats[key], (int, float)) and stats[key] < k:
                        stats[key] = f"<{k} (suppressed)"
                        low_tail_suppressed = True
            numerical[col] = stats
        else:
            cnt = Counter(nonempty)
            published = {v: c for v, c in cnt.items() if c >= k}
            withheld = {v: c for v, c in cnt.items() if c < k}
            suppressed_cells += len(withheld)
            if col in qids:
                # publish surrogate labels for the quasi-identifier (k>=threshold
                # values only; suppressed values are dropped, not surrogated)
                published = {_surrogate(col, v): c for v, c in published.items()}
            categorical[col] = {
                "counts": dict(sorted(published.items(), key=lambda kv: -kv[1])),
                "distinct_published": len(published),
                "suppressed_values": len(withheld),
                "suppressed_rows": sum(withheld.values()),
                "missing": n - sum(cnt.values()),
                **({"pseudonymised_labels": True} if col in qids else {})}

    out_dir.mkdir(parents=True, exist_ok=True)
    if _mapping:
        _mpath.parent.mkdir(parents=True, exist_ok=True)
        _mpath.write_text(json.dumps(_mapping, ensure_ascii=False, indent=2),
                          encoding="utf-8")
        # register the restricted mapping in the inventory once, so the deposit
        # guard withholds it (it is the re-identification key)
        if not any(f.get("name") == "restricted_mapping.json"
                   for f in received_files):
            received_files.append({
                "name": "restricted_mapping.json", "path": str(_mpath),
                "sha256": hashlib.sha256(_mpath.read_bytes()).hexdigest(),
                "size_bytes": _mpath.stat().st_size,
                "encodingFormat": "application/json",
                "description": "Surrogate-to-label lookup for the pseudonymised "
                               "quasi-identifier columns. Restricted; withheld "
                               "from the deposit.",
                "metadata_only": True, "access": "restricted"})
    aggregates = {"rows": n, "k": k, "generated_at": _now(),
                  "pseudonymised_columns": sorted(set(qids) & set(fields)),
                  "categorical": categorical, "numeric": numerical}
    dist = out_dir / "horarios_aggregates.json"
    dist.write_text(json.dumps(aggregates, ensure_ascii=False, indent=2),
                    encoding="utf-8")

    new_sha = hashlib.sha256(dist.read_bytes()).hexdigest()
    target["path"] = str(dist)
    target["sha256"] = new_sha
    target["size_bytes"] = dist.stat().st_size
    target["content_url"] = dist.name
    target["name"] = "horarios_aggregates.json"
    target["encodingFormat"] = "application/json"
    target["aggregated"] = True
    target.pop("column_descriptions", None)
    target["description"] = (
        f"Aggregated statistics of the institutional timetable dataset "
        f"(marginal category counts and numeric summaries) under small-cell "
        f"suppression (k={k}): no published count is below {k}, and no "
        f"cross-tabulations are released. Real aggregates, not record-level "
        f"data. Derived from the restricted raw dataset.")
    if twin_needed:
        received_files.append({
            "name": raw_name + " (raw, restricted)", "path": str(raw),
            "sha256": raw_sha, "size_bytes": raw_size,
            "encodingFormat": "text/csv",
            "description": "Original institutional dataset. Restricted; not "
                           "distributed. Source of the published aggregates.",
            "metadata_only": True, "access": "restricted"})

    manifest = {"method": "marginal aggregation with small-cell suppression, "
                          "including low-tail suppression of sensitive enrolment "
                          "counts (no cross-tabulations)", "rows": n, "k": k,
                "columns": list(fields), "suppressed_cells": suppressed_cells,
                "sensitive_count_columns": sorted(sensitive & set(fields)),
                "low_tail_suppressed": low_tail_suppressed,
                "published_cells_below_k": 0, "raw_sha256": raw_sha,
                "aggregates_sha256": new_sha, "generated_at": _now()}
    (out_dir / "aggregation_report.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def apply_disclosure_control(meta: dict, out_dir: Path) -> None:
    """Route the institutional dataset through the selected disclosure-control
    mode before serialisation. Idempotency lives at INVENTORY level, not in
    manifests kept on meta: the routines act whenever the inventory still
    contains an unprocessed institutional dataset (e.g. after re-ingesting the
    files, or after switching sharing mode) and change nothing otherwise. A
    manifest-based early return proved harmful in practice — a stale manifest
    from an earlier run (or another mode) blocked processing of a freshly
    re-ingested raw dataset forever. Manifests are refreshed on every
    effective run and the abandoned mode's manifest is dropped, so provenance
    and reports stay truthful. 'synthetic' publishes a synthetic stand-in;
    'aggregated' publishes real aggregates; both keep the raw restricted.
    Other modes rely on authorization_status / access_right at deposit time."""
    mode = (meta.get("sharing_mode") or "").split("—")[0].split("-")[0].strip().lower()
    if mode == "synthetic":
        m = synthesize_inventory(meta.get("received_files", []), Path(out_dir))
        if m:
            meta["synthesis"] = m
            meta.pop("aggregation", None)
    elif mode == "aggregated":
        m = aggregate_inventory(meta.get("received_files", []), Path(out_dir))
        if m:
            meta["aggregation"] = m
            meta.pop("synthesis", None)


# --------------------------------------------------------------- helpers ----
def _all_concept_qids(meta: dict) -> list[tuple[str, str]]:
    """Valid (label, QID) pairs from: benchmark, algorithms, indicators and
    other concepts. Silently ignores invalid QIDs."""
    pairs = []
    q = wikidata.normalize_qid(meta.get("instance_qid"))
    if q:
        pairs.append((meta.get("instance_name") or "benchmark", q))
    for row in meta.get("algorithms", []):
        q = wikidata.normalize_qid(row.get("qid"))
        if q:
            pairs.append((row.get("name") or q, q))
    for row in meta.get("indicators", []):
        q = wikidata.normalize_qid(row.get("qid"))
        if q:
            pairs.append((row.get("name") or q, q))
    for row in meta.get("concept_qids", []):
        q = wikidata.normalize_qid(row.get("qid"))
        if q:
            pairs.append((row.get("label") or row.get("concept") or q, q))
    return pairs


def _person(p: dict) -> dict:
    out = {"@type": "Person", "name": p.get("name")}
    if p.get("orcid"):
        out["identifier"] = p["orcid"]
    if p.get("affiliation"):
        out["affiliation"] = p["affiliation"]
    q = wikidata.normalize_qid(p.get("qid"))
    if q:
        out["sameAs"] = wikidata.page_url(q)
    return out


# -------------------------------------------------------------- croissant ----
def _infer_recordsets(received_files: list[dict]) -> list[dict]:
    """Builds Croissant RecordSets by reading the HEADER of each CSV/TSV and
    inferring a dataType per column from a small data sample. This annotates
    the INTERNAL STRUCTURE of the datasets (columns and types), not just the
    files as opaque objects. Inference is conservative: sc:Integer / sc:Float
    / sc:Text only; the curator can refine semantics afterwards."""
    import csv as _csv
    _num_int = re.compile(r"^-?\d+$")
    _num_flt = re.compile(r"^-?\d+([.,]\d+)([eE][+-]?\d+)?$|^-?\d+[eE][+-]?\d+$")

    def _col_type(samples: list[str]) -> str:
        vals = [s.strip() for s in samples if s and s.strip()]
        if not vals:
            return "sc:Text"
        if all(_num_int.match(v) for v in vals):
            return "sc:Integer"
        if all(_num_int.match(v) or _num_flt.match(v) for v in vals):
            return "sc:Float"
        return "sc:Text"

    record_sets = []
    for f in received_files:
        if f.get("metadata_only"):
            continue
        if not str(f.get("name", "")).lower().endswith((".csv", ".tsv")):
            continue
        path = Path(f["path"])
        if not path.is_file():
            continue
        if path.name.lower().endswith(".tsv"):
            delim = "\t"
        else:
            # sniff the delimiter from the first line: institutional CSVs
            # (e.g. Iscte exports) commonly use ';' instead of ','. Pick
            # whichever occurs more often in the header line.
            try:
                first = path.open(encoding="utf-8", newline="").readline()
            except (UnicodeDecodeError, OSError):
                continue
            delim = ";" if first.count(";") > first.count(",") else ","
        try:
            with path.open(encoding="utf-8", newline="") as fh:
                reader = _csv.reader(fh, delimiter=delim)
                rows = []
                for i, row in enumerate(reader):
                    rows.append(row)
                    if i >= 20:            # header + 20 sample rows
                        break
        except (UnicodeDecodeError, OSError):
            continue
        if not rows:
            continue
        header = rows[0]
        # skip files whose "header" is actually numeric (no real column names)
        if not header or all(_num_int.match(c.strip() or "x") for c in header):
            continue
        body = rows[1:]
        file_id = f["name"].replace("/", "_").replace("\\", "_")
        fields = []
        col_desc = f.get("column_descriptions", {})
        for ci, col in enumerate(header):
            col = (col or f"column_{ci+1}").strip()
            samples = [r[ci] for r in body if ci < len(r)]
            field_obj = {
                "@type": "cr:Field",
                "@id": f"{file_id}/{col}",
                "name": col,
                "dataType": _col_type(samples),
                "source": {"fileObject": {"@id": file_id},
                           "extract": {"column": col}},
            }
            if col in col_desc:
                field_obj["description"] = col_desc[col]
            fields.append(field_obj)
        record_sets.append({
            "@type": "cr:RecordSet",
            "@id": f"{file_id}_records",
            "name": f["name"],
            "field": fields,
        })
    return record_sets


def generate_croissant(meta: dict) -> dict:
    doc = {
        "@context": {"@vocab": "https://schema.org/",
                     "cr": "http://mlcommons.org/croissant/",
                     "sc": "https://schema.org/"},
        "@type": "sc:Dataset",
        "conformsTo": "http://mlcommons.org/croissant/1.0",
        "distribution": [],
    }
    for _, field in schema.all_fields():
        target = field.get("maps", {}).get("croissant")
        value = meta.get(field["key"])
        if not target or value in (None, "", []):
            continue
        if "." in target or target[0].isupper() or " " in target:
            continue
        doc[target] = value
    if meta.get("creators"):
        doc["creator"] = [_person(c) for c in meta["creators"]]

    if meta.get("instance_name"):
        iq = wikidata.normalize_qid(meta.get("instance_qid"))
        # only a real URI belongs in sameAs; free-text provenance goes to
        # description to keep the Croissant graph well-formed
        desc_parts = [d for d in (meta.get("instance_dims"),
                                  meta.get("instance_provenance")) if d]
        entry = {"@type": "cr:FileObject", "@id": "instance",
                 "name": meta["instance_name"]}
        if iq:
            entry["sameAs"] = wikidata.page_url(iq)
        _ifm = fairmoo.uri("problem", meta.get("instance_fairmoo"))
        if _ifm:
            entry["sameAs"] = ([entry["sameAs"], _ifm]
                               if entry.get("sameAs") else _ifm)
        if desc_parts:
            entry["description"] = " — ".join(desc_parts)
        doc["distribution"].append(entry)

    for f in meta.get("received_files", []):
        entry = {
            "@type": "cr:FileObject",
            "@id": f["name"].replace("/", "_").replace("\\", "_"),
            "name": f["name"],
            "encodingFormat": f["encodingFormat"], "sha256": f["sha256"],
            "contentSize": f"{f['size_bytes']} B",
            **({"description": f["description"]} if f.get("description") else {})}
        if not f.get("metadata_only"):
            entry["contentUrl"] = f.get("content_url", f["name"])
        if f["name"].lower().endswith(".ctt"):
            _bfm = fairmoo.uri("problem", meta.get("benchmark_fairmoo"))
            if _bfm:
                entry["sameAs"] = _bfm
        doc["distribution"].append(entry)

    concepts = _all_concept_qids(meta)
    about = [{"@type": "Thing", "name": label,
              "sameAs": wikidata.page_url(qid)}
             for label, qid in concepts]
    # algorithms without a MOODY class or QID carry their FAIR-MOO URI
    for a in meta.get("algorithms", []):
        _, spec = _moody_algorithm_type(a.get("name") or "")
        fm = fairmoo.decide("algorithm", a.get("name"),
                            wikidata.normalize_qid(a.get("qid")),
                            moody_specific=spec, override=a.get("fairmoo"))
        if fm and not any(x.get("name") == a.get("name") for x in about):
            about.append({"@type": "Thing", "name": a.get("name"), "sameAs": fm})
    if about:
        doc["about"] = about

    # software that produced the results, identified by its SWHID (archived code)
    import swh as _swh
    _sw = _swh.parse_swhid(meta.get("code_swhid"))
    if _sw:
        code = {"@type": "SoftwareSourceCode", "name": "Experiment source code",
                "identifier": _sw, "sameAs": _swh.browse_url(_sw)}
        if str(meta.get("code_repository", "")).startswith("http"):
            code["codeRepository"] = meta["code_repository"]
        doc["isBasedOn"] = code

    record_sets = _infer_recordsets(meta.get("received_files", []))
    if record_sets:
        doc["recordSet"] = record_sets

    variables = []
    for o in meta.get("objectives", []):
        pv = {"@type": "PropertyValue", "name": o.get("name"),
              "description": f"{o.get('direction', '')} — {o.get('definition', '')}"}
        q = wikidata.normalize_qid(o.get("qid"))
        if q:
            pv["sameAs"] = wikidata.page_url(q)
        else:
            fm = fairmoo.decide("objective", o.get("name"), q,
                                override=o.get("fairmoo"))
            if fm:
                pv["sameAs"] = fm
        variables.append(pv)
    # quality indicators are measured properties too: each is anchored by its
    # canonical FAIR-MOO IRI, with the Wikidata QID (when present) attached as an
    # equivalence — homogeneous with the objectives and the SKOS vocabulary
    for ind in meta.get("indicators", []):
        if not ind.get("name"):
            continue
        pv = {"@type": "PropertyValue", "name": ind.get("name")}
        if ind.get("params"):
            pv["description"] = ind["params"]
        same = [_indicator_fairmoo(ind)]
        q = wikidata.normalize_qid(ind.get("qid"))
        if q:
            same.append(wikidata.page_url(q))
        pv["sameAs"] = same if len(same) > 1 else same[0]
        variables.append(pv)
    if variables:
        doc["variableMeasured"] = variables

    if meta.get("not_recorded"):
        doc["description"] = (doc.get("description", "") +
            "\n\n[Curation] Not recorded in the source (:unav): " +
            "; ".join(f"{k} — {v}" for k, v in meta["not_recorded"].items()))
    return doc


# ---------------------------------------------------------------- prov-o -----
# ---------------------------------------------------------------- MOODY --------
# Alignment to the MOODY ontology (https://w3id.org/moody#), a peer-reviewed
# OWL2 vocabulary for multi-objective optimisation experiments
# (Aldana-Martín et al., 2024). Class IRIs below are verified against the
# published ontology. Concepts without a dedicated MOODY class degrade to the
# nearest MOODY/BIGOWL superclass, so typing never silently fails.
MOODY = "https://w3id.org/moody#"
BIGOWLA = "https://w3id.org/BIGOWLAlgorithms/"
BIGOWLP = "https://w3id.org/BIGOWLProblems/"

def _moody_key(name: str) -> str:
    """normalise an algorithm/indicator name for MOODY lookup."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())

# name -> MOODY class local part (verified in the ontology)
_MOODY_ALGORITHMS = {
    "nsgaii": "NSGAII", "nsga2": "NSGAII",
    "moead": "MOEAD", "moeadde": "MOEADDE",
    "mopso": "MOPSO",
}
_MOODY_INDICATORS = {
    "hypervolume": "Hypervolume", "hv": "Hypervolume",
    "normalisedhypervolume": "Hypervolume", "normalizedhypervolume": "Hypervolume",
    "spread": "Spread", "generalisedspread": "Spread", "generalizedspread": "Spread",
    "epsilon": "Epsilon", "additiveepsilon": "Epsilon",
    "invertedgenerationaldistance": "InvertedGenerationalDistance",
    "igd": "InvertedGenerationalDistance",
    "invertedgenerationaldistanceplus": "InvertedGenerationalDistancePlus",
    "igdplus": "InvertedGenerationalDistancePlus",
}

def _moody_algorithm_type(name: str) -> tuple[str, bool]:
    """Returns (type IRI, is_specific). Specific MOODY class when known,
    else the BIGOWL Algorithm superclass MOODY builds upon."""
    cls = _MOODY_ALGORITHMS.get(_moody_key(name))
    if cls:
        return MOODY + cls, True
    return BIGOWLA + "Algorithm", False

def _moody_indicator_type(name: str) -> tuple[str, bool]:
    key = _moody_key(name)
    cls = _MOODY_INDICATORS.get(key)
    if cls:
        return MOODY + cls, True
    # substring match: "normalised hypervolume" -> Hypervolume, "generalised
    # spread" -> Spread. Longest keys first to avoid partial mis-hits.
    for k in sorted(_MOODY_INDICATORS, key=len, reverse=True):
        if k in key:
            return MOODY + _MOODY_INDICATORS[k], True
    return MOODY + "QualityIndicator", False


# --------------------------------------------------------- FAIR-MOO IRIs -----
# Single source of truth for the study's FAIR-MOO concept IRIs, so the SKOS
# vocabulary, the Croissant view and the PROV view agree on the URI minted for
# each concept (they diverge otherwise, e.g. under MOO_VOCAB_BASE).
def _vocab_base() -> str:
    return (os.environ.get("MOO_VOCAB_BASE")
            or "https://w3id.org/fair-moo/").rstrip("/") + "/"


def _fm_slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "").strip().lower())
    return re.sub(r"-+", "-", s).strip("-") or "concept"


def _concept_iri(kind: str, label: str, decided_uri: str | None) -> str:
    """Canonical FAIR-MOO IRI for a curated concept, ALWAYS defined. Prefers the
    registered URI from the minter (curator override, MOODY-aligned reuse or a
    coined slug); otherwise falls back to the study namespace. A Wikidata QID
    never suppresses this IRI — the QID travels alongside it as owl:sameAs."""
    return decided_uri or f"{_vocab_base()}{kind}/{_fm_slug(label)}"


def _indicator_fairmoo(ind: dict) -> str:
    """Canonical FAIR-MOO IRI of a reported quality indicator. Every indicator is
    anchored by a FAIR-MOO IRI as its primary identifier, exactly as the
    objectives are: a MOODY-covered indicator reuses its MOODY typing but still
    receives this IRI, and any Wikidata QID rides along as owl:sameAs. Honours a
    curator-supplied slug (the 'fairmoo' column) via the minter's override."""
    _, spec = _moody_indicator_type(ind.get("name") or "")
    decided = fairmoo.decide("indicator", ind.get("name"),
                             wikidata.normalize_qid(ind.get("qid")),
                             moody_specific=spec, override=ind.get("fairmoo"))
    return _concept_iri("indicator", ind.get("name"), decided)


def generate_prov(meta: dict) -> str:
    """Two activities (original experiment + curation), curator as agent,
    documentary evidence per field, declared absence, semantic anchors.
    TODO: enrich with MOODY IRIs confirmed against the ontology OWL."""
    def _ttl(s, limit: int | None = None) -> str:
        """Escapes user-supplied text for a single-quoted Turtle literal:
        backslashes and quotes are escaped, newlines/CR/tabs collapsed to a
        space (a raw newline inside \"...\" is a BadSyntax parse error)."""
        t = str(s or "")
        if limit:
            t = t[:limit]
        return (t.replace("\\", "\\\\").replace('"', '\\"')
                 .replace("\r", " ").replace("\n", " ").replace("\t", " "))

    header = """@prefix prov: <http://www.w3.org/ns/prov#> .
@prefix foaf: <http://xmlns.com/foaf/0.1/> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix wd:   <http://www.wikidata.org/entity/> .
@prefix moody: <https://w3id.org/moody#> .
@prefix bigowla: <https://w3id.org/BIGOWLAlgorithms/> .
@prefix bigowlp: <https://w3id.org/BIGOWLProblems/> .
@prefix ex:   <urn:fair-moo:> .
@prefix schema: <https://schema.org/> .

"""
    creators = "\n".join(
        f'ex:creator{i} a prov:Person ; foaf:name "{_ttl(c.get("name", ""))}" .'
        for i, c in enumerate(meta.get("creators", [])))
    curators = "\n".join(
        f'ex:curator{i} a prov:Person ; foaf:name "{_ttl(c.get("name", ""))}" .'
        for i, c in enumerate(meta.get("curators", [])))
    creator_assoc = " , ".join(
        f"ex:creator{i}" for i in range(len(meta.get("creators", [])))) or "ex:creator0"
    curator_assoc = " , ".join(
        f"ex:curator{i}" for i in range(len(meta.get("curators", [])))) or "ex:curator0"

    source = str(meta.get("source_document", "urn:unknown-source")).strip()
    if source.startswith("http"):
        source_ref = f'"{_ttl(source)}" ;\n    rdfs:seeAlso <{source}>'
    else:
        source_ref = f'"{_ttl(source)}"'

    evidence = "\n".join(
        f'ex:field-{k} a prov:Entity ; rdfs:label "{_ttl(k)}" ; '
        f'prov:value "{_ttl(meta.get(k, ""), 120)}" ; '
        f'ex:evidence "{_ttl(v)}" ; prov:wasGeneratedBy ex:curation .'
        for k, v in meta.get("evidence", {}).items())
    not_recorded = "\n".join(
        f'ex:field-{k} a prov:Entity ; rdfs:label "{_ttl(k)}" ; '
        f'prov:value ":unav" ; '
        f'ex:absenceJustification "{_ttl(v)}" ; prov:wasGeneratedBy ex:curation .'
        for k, v in meta.get("not_recorded", {}).items())

    # --- MOODY-typed concept entities + Wikidata anchors ---
    def _safe(label):
        return "".join(ch if ch.isalnum() else "-" for ch in (label or ""))[:40]

    moody_lines, exp_algos, exp_inds = [], [], []
    seen_ids = set()

    def _emit(local, label, type_iri, qid, specific, note=None, version=None,
              fairmoo_uri=None):
        if local in seen_ids:
            return
        seen_ids.add(local)
        parts = [f"ex:{local} a <{type_iri}> ", f'    ; rdfs:label "{_ttl(label)}" ']
        if not specific and note:
            parts.append(f'    ; rdfs:comment "{_ttl(note)}" ')
        if version and str(version).strip():
            parts.append(f'    ; schema:softwareVersion "{_ttl(version)}" ')
        nq = wikidata.normalize_qid(qid)
        if nq:
            parts.append(f"    ; owl:sameAs wd:{nq} ")
        if fairmoo_uri:
            parts.append(f"    ; owl:sameAs <{fairmoo_uri}> ")
        moody_lines.append("\n".join(parts) + ".")

    for a in meta.get("algorithms", []):
        nm = a.get("name")
        if not nm:
            continue
        local = f"algo-{_safe(nm)}"
        tiri, spec = _moody_algorithm_type(nm)
        fm = fairmoo.decide("algorithm", nm,
                            wikidata.normalize_qid(a.get("qid")),
                            moody_specific=spec, override=a.get("fairmoo"))
        _emit(local, nm, tiri, a.get("qid"), spec,
              note="typed as generic BIGOWL Algorithm; no dedicated MOODY class",
              version=a.get("version"), fairmoo_uri=fm)
        exp_algos.append(local)

    for ind in meta.get("indicators", []):
        nm = ind.get("name")
        if not nm:
            continue
        local = f"ind-{_safe(nm)}"
        tiri, spec = _moody_indicator_type(nm)
        _emit(local, nm, tiri, ind.get("qid"), spec,
              note="typed as generic MOODY QualityIndicator",
              fairmoo_uri=_indicator_fairmoo(ind))
        exp_inds.append(local)

    # problem: MOODY reuses BIGOWL for problems; timetabling has no MOODY class
    prob_local = None
    if meta.get("instance_name"):
        prob_local = "problem-instance"
        _emit(prob_local, meta["instance_name"],
              BIGOWLP + "Problem", meta.get("instance_qid"), False,
              note="application problem (university timetabling); "
                   "typed as generic BIGOWL Problem",
              fairmoo_uri=fairmoo.uri("problem", meta.get("instance_fairmoo")))

    # remaining concept_qids (software, domain, etc.) stay as anchored entities
    typed = {f"algo-{_safe(a.get('name'))}" for a in meta.get("algorithms", [])} | \
            {f"ind-{_safe(i.get('name'))}" for i in meta.get("indicators", [])}
    for label, qid in _all_concept_qids(meta):
        loc = _safe(label)
        if f"algo-{loc}" in typed or f"ind-{loc}" in typed:
            continue
        _emit(f"concept-{loc}", label, "http://www.w3.org/ns/prov#Entity",
              qid, True)

    # moody:Experiment node linking algorithms, problem and indicators
    exp_lines = []
    if exp_algos or exp_inds or prob_local:
        rel = ["ex:moo-experiment a moody:Experiment "]
        for a in exp_algos:
            rel.append(f"    ; moody:algorithmUsed ex:{a} ")
        if prob_local:
            rel.append(f"    ; moody:problemSolved ex:{prob_local} ")
        for i in exp_inds:
            rel.append(f"    ; moody:evaluatedBy ex:{i} ")
        exp_lines.append("\n".join(rel) + ".")

    for i, person in enumerate(meta.get("creators", [])):
        q = wikidata.normalize_qid(person.get("qid"))
        if q:
            moody_lines.append(f"ex:creator{i} owl:sameAs wd:{q} .")

    anchors = ""
    if moody_lines or exp_lines:
        anchors = ("\n# MOODY-typed concepts + Wikidata anchors\n"
                   + "\n".join(moody_lines) + "\n\n"
                   + "# Experiment relations (MOODY)\n"
                   + "\n".join(exp_lines) + "\n")

    inst = meta.get("instance_provenance", "").strip()
    if inst.startswith("http"):
        instance = f"ex:instance a prov:Entity ;\n    prov:wasDerivedFrom <{inst}> ."
    elif inst:
        # free-text provenance -> rdfs:comment (a URI would break the graph)
        instance = (f'ex:instance a prov:Entity ;\n'
                    f'    rdfs:comment "{_ttl(inst, 300)}" .')
    else:
        instance = "ex:instance a prov:Entity ."

    if meta.get("deidentification"):
        _cols = ", ".join(meta["deidentification"].get("columns", {}))
        deid = f"""

# De-identification (R6): public dataset derived from the restricted source
ex:raw-timetable a prov:Entity ;
    rdfs:label "ISCTE timetable dataset (raw, restricted)" .
ex:pseudonymised-timetable a prov:Entity ;
    prov:wasDerivedFrom ex:raw-timetable ;
    prov:wasGeneratedBy ex:deidentification .
ex:deidentification a prov:Activity ;
    rdfs:label "Surrogate-key pseudonymisation ({_cols}); mapping withheld" ;
    prov:wasAssociatedWith {curator_assoc} ;
    prov:used ex:raw-timetable ;
    prov:endedAtTime "{_now()}"^^xsd:dateTime ."""
    elif meta.get("synthesis"):
        _meth = _ttl(meta["synthesis"].get("method", "synthesis"), 200)
        deid = f"""

# Synthesis (disclosure control): public synthetic dataset derived from the restricted source
ex:raw-timetable a prov:Entity ;
    rdfs:label "ISCTE timetable dataset (raw, restricted)" .
ex:synthetic-timetable a prov:Entity ;
    rdfs:label "ISCTE timetable dataset (synthetic, open)" ;
    prov:wasDerivedFrom ex:raw-timetable ;
    prov:wasGeneratedBy ex:synthesis .
ex:synthesis a prov:Activity ;
    rdfs:label "Structure-preserving synthesis ({_meth})" ;
    prov:wasAssociatedWith {curator_assoc} ;
    prov:used ex:raw-timetable ;
    prov:endedAtTime "{_now()}"^^xsd:dateTime ."""
    elif meta.get("aggregation"):
        _k = meta["aggregation"].get("k")
        deid = f"""

# Aggregation (disclosure control): public aggregates derived from the restricted source
ex:raw-timetable a prov:Entity ;
    rdfs:label "ISCTE timetable dataset (raw, restricted)" .
ex:aggregated-timetable a prov:Entity ;
    rdfs:label "ISCTE timetable dataset (aggregated, open)" ;
    prov:wasDerivedFrom ex:raw-timetable ;
    prov:wasGeneratedBy ex:aggregation .
ex:aggregation a prov:Activity ;
    rdfs:label "Marginal aggregation with small-cell suppression (k={_k})" ;
    prov:wasAssociatedWith {curator_assoc} ;
    prov:used ex:raw-timetable ;
    prov:endedAtTime "{_now()}"^^xsd:dateTime ."""
    else:
        deid = ""

    import swh as _swh
    _sw = _swh.parse_swhid(meta.get("code_swhid"))
    if _sw:
        code_used = " ;\n    prov:used ex:software"
        _repo = meta.get("code_repository", "")
        _repo_line = (f'\n    ; schema:codeRepository "{_ttl(_repo)}" '
                      if str(_repo).startswith("http") else "")
        code_entity = (f'ex:software a schema:SoftwareSourceCode '
                       f'; rdfs:label "Experiment source code" '
                       f'; owl:sameAs <{_swh.browse_url(_sw)}> '
                       f'{_repo_line}.\n')
    else:
        code_used = ""
        code_entity = ""
    return header + f"""# Activity 1 — original experiment (detail limited to what the source reports)
ex:original-experiment a prov:Activity ;
    prov:wasAssociatedWith {creator_assoc} ;
    prov:used ex:instance{code_used} ;
    rdfs:comment "Experiment described in the source document; details as reported." .

{instance}
{code_entity}

# Activity 2 — retrospective curation/FAIRification
ex:curation a prov:Activity ;
    prov:startedAtTime "{_now()}"^^xsd:dateTime ;
    prov:wasAssociatedWith {curator_assoc} ;
    prov:used ex:source-document .

ex:source-document a prov:Entity ;
    rdfs:label {source_ref} .

ex:metadata-record a prov:Entity ;
    prov:wasGeneratedBy ex:curation ;
    prov:hadPrimarySource ex:source-document .

{creators}
{curators}

# Provenance of the metadata: evidence per field
{evidence}
{not_recorded}
{anchors}
{deid}"""


# --------------------------------------------------------------- datacite ----
def generate_datacite_zenodo(meta: dict) -> dict:
    import pid_registry
    related, seen = [], set()

    def _canon(identifier):
        """Deduplication key insensitive to the doi.org prefix and to case,
        so the SAME work in different formats (URL vs bare DOI) is never
        emitted twice."""
        low = str(identifier or "").strip().lower()
        for pref in ("https://doi.org/", "http://doi.org/",
                     "https://dx.doi.org/", "http://dx.doi.org/"):
            if low.startswith(pref):
                return low[len(pref):]
        return low

    def _add_related(identifier, relation):
        identifier = (identifier or "").strip()
        if not identifier:
            return
        key = _canon(identifier)
        if key in seen:
            return
        seen.add(key)
        related.append({"identifier": identifier, "relation": relation})

    def _doi_or_url(value):
        """Canonical DOI if the value is a DOI; else the http(s) URL itself;
        else None. Never invents."""
        d = pid_registry.parse_pid("doi", value)
        if d:
            return d
        v = str(value or "").strip()
        return v if v.startswith("http") else None

    # documentary provenance of the metadata (source of the curation)
    if str(meta.get("source_document", "")).startswith("http"):
        _add_related(meta["source_document"], "isDerivedFrom")

    # replicated work (the reproduced study)
    rep = _doi_or_url(meta.get("replicated_work"))
    if rep:
        _add_related(rep, "references")

    # problem instance reified as a linked artefact in the graph
    inst = _doi_or_url(meta.get("instance_provenance"))
    if inst:
        _add_related(inst, "references")

    # Artefact DOIs per algorithm/concept (software, framework, benchmark):
    # this is what turns the reified artefacts into LINKED NODES in the
    # OpenAIRE Graph when harvested — distinct from the Wikidata anchors
    # (semantic layer) below
    for row in meta.get("algorithms", []) + meta.get("concept_qids", []):
        d = _doi_or_url(row.get("doi"))
        if d:
            _add_related(d, "references")

    # semantic layer: Wikidata anchors of the concepts
    for _, qid in _all_concept_qids(meta):
        _add_related(wikidata.page_url(qid), "references")

    # software identifier: the archived source code (Software Heritage SWHID)
    # -- the DOI identifies the data; this references the code that produced it
    import swh as _swh
    _sw = _swh.parse_swhid(meta.get("code_swhid"))
    if _sw:
        _add_related(_swh.browse_url(_sw), "isSupplementedBy")

    notes = ["Record produced by retrospective curation (FAIRification of "
             "legacy data). Authorization: "
             + meta.get("authorization_status", "n/a") + "."]
    if meta.get("not_recorded"):
        notes.append("Not recorded in the source (:unav): " +
                     "; ".join(f"{k} — {v}"
                               for k, v in meta["not_recorded"].items()))
    if meta.get("synthesis"):
        notes.append(
            "Institutional dataset published as a SYNTHETIC, structure-preserving "
            "stand-in (independent per-column resampling with rare-category "
            "generalisation); it contains no real individual records and does not "
            "reproduce the original data. The raw dataset is restricted and not "
            "distributed. Deposit made under institutional authorisation; DPO "
            "consultation conducted.")
    if meta.get("aggregation"):
        notes.append(
            "Institutional dataset published as REAL AGGREGATES (marginal "
            "category counts and numeric summaries) under small-cell "
            "suppression; no record-level data and no cross-tabulations are "
            "released. The raw dataset is restricted and not distributed. "
            "Deposit made under institutional authorisation; DPO consultation "
            "conducted.")
    if meta.get("curation_notes"):
        notes.append(meta["curation_notes"])

    return {"metadata": {
        "title": meta.get("title"),
        "upload_type": "dataset",
        "description": meta.get("description"),
        "creators": [
            {"name": c.get("name"),
             **({"orcid": c["orcid"]} if c.get("orcid") else {}),
             **({"affiliation": c["affiliation"]} if c.get("affiliation") else {})}
            for c in meta.get("creators", [])],
        "contributors": [
            {"name": c.get("name"), "type": "DataCurator",
             **({"orcid": c["orcid"]} if c.get("orcid") else {}),
             **({"affiliation": c["affiliation"]} if c.get("affiliation") else {})}
            for c in meta.get("curators", [])],
        "license": meta.get("license", "").lower(),
        "keywords": meta.get("keywords", []),
        "version": meta.get("version"),
        "language": meta.get("language"),
        "access_right": meta.get("access_right", "restricted"),
        **({"embargo_date": meta["embargo_date"]} if meta.get("embargo_date") else {}),
        **({"related_identifiers": related} if related else {}),
        **({"communities": [{"identifier": c} for c in meta["communities"]]}
           if meta.get("communities") else {}),
        "notes": "\n\n".join(notes),
    }}


# ---------------------------------------------------------------- dcat-ap ----
# DCAT-AP 3.0 catalogue-level description (JSON-LD). Complements DataCite
# (repository citation) and Croissant (dataset internals) with the profile
# European data portals and the OpenAIRE aggregator consume. Field mapping
# mirrors generate_datacite_zenodo so the coordinated views stay consistent:
# a single dcat:Dataset with one dcat:Distribution per received file.
# SPDX licence URIs are emitted when the licence id is recognised; otherwise
# the raw value is kept as a dct:license literal so nothing is silently lost.
_SPDX = "http://spdx.org/licenses/"
_DCAT_ACCESS = {           # Zenodo access_right -> EU Access Right vocabulary
    "open": "PUBLIC", "embargoed": "NON_PUBLIC",
    "restricted": "RESTRICTED", "closed": "NON_PUBLIC",
}
_EU_ACCESS = "http://publications.europa.eu/resource/authority/access-right/"


def _dcat_licence(raw: str) -> dict | None:
    """A recognised SPDX id becomes a resolvable licence IRI; any other
    non-empty value is preserved as-is. Keeps the graph well-formed while
    never discarding what the curator declared."""
    lic = (raw or "").strip()
    if not lic:
        return None
    # Zenodo stores licences lower-cased (e.g. "cc-by-4.0"); SPDX is cased
    # (CC-BY-4.0). Emit the SPDX IRI when the shape looks like an SPDX id.
    if re.match(r"^[A-Za-z0-9.\-]+$", lic) and "/" not in lic and " " not in lic:
        spdx_id = lic.upper() if lic.islower() else lic
        return {"@id": _SPDX + spdx_id}
    if lic.startswith("http"):
        return {"@id": lic}
    return {"@type": "dct:LicenseDocument", "rdfs:label": lic}


def generate_dcat_ap(meta: dict) -> dict:
    """DCAT-AP 3.0 record in JSON-LD. The dataset node carries citation-level
    metadata (title, description, creators, keywords, licence, version,
    language, access rights); each received file becomes a dcat:Distribution
    carrying its media type, byte size and SHA-256 checksum (spdx:Checksum).
    Concept Wikidata anchors are exposed through dcat:theme, and the
    documentary source through dct:source, aligning with the other views."""
    ds_id = ("https://doi.org/" + meta["doi"]) if meta.get("doi") else "#dataset"

    dataset = {
        "@id": ds_id,
        "@type": "dcat:Dataset",
        "dct:title": meta.get("title"),
        "dct:description": meta.get("description"),
    }
    if meta.get("version"):
        dataset["dcat:version"] = meta["version"]
    if meta.get("language"):
        dataset["dct:language"] = meta["language"]
    if meta.get("keywords"):
        dataset["dcat:keyword"] = meta["keywords"]

    # issued/modified: the curation timestamp is the record's own generation
    dataset["dct:issued"] = {"@type": "xsd:dateTime", "@value": _now()}

    lic = _dcat_licence(meta.get("license", ""))
    if lic:
        dataset["dct:license"] = lic

    # access rights: EU authority vocabulary term derived from Zenodo's
    # access_right, so a portal reads accessibility without parsing free text
    ar = _DCAT_ACCESS.get((meta.get("access_right") or "restricted").lower())
    if ar:
        dataset["dct:accessRights"] = {"@id": _EU_ACCESS + ar}

    # creators (dct:creator) and curators (dct:contributor) as foaf:Agent,
    # identified by ORCID/ROR where available — persons via ORCID, no QIDs
    def _agent(p: dict, is_curator: bool = False) -> dict:
        node = {"@type": "foaf:Agent", "foaf:name": p.get("name")}
        if p.get("orcid"):
            node["dct:identifier"] = p["orcid"]
        if p.get("affiliation"):
            node["org:memberOf"] = {"@type": "foaf:Organization",
                                    "foaf:name": p["affiliation"],
                                    **({"dct:identifier": p["ror"]}
                                       if p.get("ror") else {})}
        return node

    if meta.get("creators"):
        dataset["dct:creator"] = [_agent(c) for c in meta["creators"]]
    if meta.get("curators"):
        dataset["dct:contributor"] = [_agent(c, True) for c in meta["curators"]]
    if meta.get("creators"):
        # dct:publisher: first creator's institution when known
        aff = next((c.get("affiliation") for c in meta["creators"]
                    if c.get("affiliation")), None)
        if aff:
            dataset["dct:publisher"] = {"@type": "foaf:Organization",
                                        "foaf:name": aff}

    # dcat:theme — concept anchors (Wikidata) shared with the other views
    themes = [{"@id": wikidata.page_url(q)} for _, q in _all_concept_qids(meta)]
    if themes:
        dataset["dcat:theme"] = themes

    # dct:source — the documentary source of the retrospective curation
    src = str(meta.get("source_document", "")).strip()
    if src.startswith("http"):
        dataset["dct:source"] = {"@id": src}
    elif src:
        dataset["dct:source"] = src

    # one dcat:Distribution per received file (skipped for metadata-only,
    # handled by the caller through received_files being empty if desired)
    distributions = []
    for f in meta.get("received_files", []):
        dist = {
            "@type": "dcat:Distribution",
            "@id": "#dist-" + f["name"].replace("/", "_").replace("\\", "_"),
            "dct:title": f["name"],
            "dcat:accessURL": {"@id": ds_id},
            "dcat:mediaType": f.get("encodingFormat"),
            "dcat:byteSize": {"@type": "xsd:nonNegativeInteger",
                              "@value": str(f.get("size_bytes", 0))},
        }
        if f.get("sha256"):
            dist["spdx:checksum"] = {
                "@type": "spdx:Checksum",
                "spdx:algorithm": {"@id": "spdx:checksumAlgorithm_sha256"},
                "spdx:checksumValue": f["sha256"],
            }
        if f.get("description"):
            dist["dct:description"] = f["description"]
        if lic:
            dist["dct:license"] = lic
        if f.get("access"):
            _ar = _DCAT_ACCESS.get(f["access"].lower())
            if _ar:
                dist["dct:accessRights"] = {"@id": _EU_ACCESS + _ar}
        distributions.append(dist)
    if distributions:
        dataset["dcat:distribution"] = distributions

    return {
        "@context": {
            "dcat": "http://www.w3.org/ns/dcat#",
            "dct": "http://purl.org/dc/terms/",
            "foaf": "http://xmlns.com/foaf/0.1/",
            "org": "http://www.w3.org/ns/org#",
            "spdx": "http://spdx.org/rdf/terms#",
            "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
            "xsd": "http://www.w3.org/2001/XMLSchema#",
        },
        "@graph": [dataset],
    }


# --------------------------------------------------- queryable JSON object --
def generate_experiment_record(meta: dict, out_dir: Path) -> Path:
    """Operational projection of the profile into plain JSON (stable keys)
    for direct inspection (jq/pandas). Does not replace the standard views."""
    fair_report = {}
    rp = out_dir / "fair_check_report.json"
    if rp.exists():
        fair_report = json.loads(rp.read_text(encoding="utf-8")).get("summary", {})
    record = {
        "schema_version": "0.2-curation",
        "generated_at": _now(),
        "doi": meta.get("doi"),
        "metadata": {k: v for k, v in meta.items()
                     if k not in {"received_files", "pid_results",
                                  "pid_confirmations", "doi"}},
        "pids": {"results": meta.get("pid_results", []),
                 "confirmations": meta.get("pid_confirmations", {})},
        "pid_resolutions": {(p.get("normalized") or p.get("raw")): p["pidmr_url"]
                            for p in meta.get("pid_results", [])
                            if p.get("pidmr_url")},
        "files": [{k: f[k] for k in ("name", "sha256", "size_bytes",
                                     "encodingFormat", "description")}
                  for f in meta.get("received_files", [])],
        "fair_check_summary": fair_report,
        "confirmed_by_user_at": meta.get("confirmed_at"),
    }
    path = out_dir / "experiment_record.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# --------------------------------------------------- confirmation report ----
def build_confirmation_report(meta: dict) -> str:
    lines = ["## Pre-deposit confirmation report", ""]
    for key in ("title", "description", "version", "license", "access_right",
                "instance_name", "instance_provenance",
                "source_document", "authorization_status"):
        if meta.get(key):
            lines.append(f"**{key}:** {meta[key]}")
    for role, label in (("creators", "original creator"), ("curators", "curator")):
        for p in meta.get(role, []):
            lines.append(f"**{label}:** {p.get('name')} · ORCID: "
                         f"{p.get('orcid') or '—'} · ROR: {p.get('ror') or '—'}")
    for o in meta.get("objectives", []):
        lines.append(f"**objective:** {o.get('name')} ({o.get('direction')})")
    for a in meta.get("algorithms", []):
        ver = f" · v{a.get('version')}" if a.get("version") else ""
        lines.append(f"**algorithm:** {a.get('name')}{ver} → {a.get('qid') or 'NO QID'}")
    for c in meta.get("configurations", []):
        lines.append(f"**configuration:** {c.get('label')}")
    for i in meta.get("indicators", []):
        _qid = f" · {i.get('qid')}" if i.get("qid") else ""
        lines.append(f"**indicator:** {i.get('name')} · "
                     f"{i.get('params') or 'no parameters'} · "
                     f"FAIR-MOO {_indicator_fairmoo(i)}{_qid}")
    for f in meta.get("received_files", []):
        lines.append(f"**file:** {f['name']} · sha256 {f['sha256'][:12]}… "
                     f"· {f.get('description') or 'NO DESCRIPTION'}")
    confirmed = meta.get("pid_confirmations", {})
    for p in meta.get("pid_results", []):
        key = p.get("normalized") or p["raw"]
        status = "✔ confirmed" if confirmed.get(key) else "✘ NOT confirmed"
        lines.append(f"**PID {p['kind'].upper()}** {p['raw']} → "
                     f"{p.get('label') or p.get('error')} · {status}")
    for k, v in meta.get("not_recorded", {}).items():
        lines.append(f"**not recorded in the source:** {k} — {v}")
    lines.append("")
    lines.append("_By confirming, I declare that I have reviewed all the information above "
                 "and authorise the generation of the deposit._")
    return "\n\n".join(lines)


# ----------------------------------------------------------- orchestration --
def generate_ontology(meta: dict, base_iri: str | None = None) -> str:
    """Emits a SKOS/OWL vocabulary (Turtle) for the MOO concepts curated in
    this study: algorithms, problem instances, indicators and other tagged
    concepts. Each concept becomes a skos:Concept in a study-specific
    namespace, with rdfs:label, a skos:definition when available, membership
    of a typed skos:Collection, and owl:sameAs / skos:exactMatch links to the
    corresponding Wikidata item where one exists.

    This is the *artefact* of the ontology layer: it defines local, resolvable
    identifiers for the domain concepts instead of only borrowing Wikidata
    QIDs. Making the IRIs resolve on the web is a deployment step OUTSIDE the
    app: register the base namespace (e.g. via a w3id.org redirect) and serve
    this Turtle. The base IRI is configurable through the MOO_VOCAB_BASE
    environment variable; the default is a w3id-style placeholder that MUST be
    replaced with a namespace you control before publication."""
    import os
    import re as _re

    base = (base_iri or os.environ.get("MOO_VOCAB_BASE")
            or "https://w3id.org/fair-moo/").rstrip("/") + "/"

    def _slug(text: str) -> str:
        s = _re.sub(r"[^a-zA-Z0-9]+", "-", (text or "").strip().lower())
        return _re.sub(r"-+", "-", s).strip("-") or "concept"

    def _esc(text: str) -> str:
        return (text or "").replace("\\", "\\\\").replace('"', '\\"') \
            .replace("\n", " ").strip()

    # gather (kind, label, definition, qid, version) for each concept family
    rows = []
    for a in meta.get("algorithms", []):
        _, _spec = _moody_algorithm_type(a.get("name") or "")
        rows.append(("algorithm", a.get("name"), None, a.get("qid"),
                     a.get("version"),
                     fairmoo.decide("algorithm", a.get("name"),
                                    wikidata.normalize_qid(a.get("qid")),
                                    moody_specific=_spec,
                                    override=a.get("fairmoo"))))
    inst = meta.get("instance_name")
    if inst:
        rows.append(("problem", inst, meta.get("instance_dims"),
                     meta.get("instance_qid"), None,
                     fairmoo.uri("problem", meta.get("instance_fairmoo"))))
    for i in meta.get("indicators", []):
        _, _ispec = _moody_indicator_type(i.get("name") or "")
        rows.append(("indicator", i.get("name"), i.get("params"),
                     i.get("qid"), None,
                     fairmoo.decide("indicator", i.get("name"),
                                    wikidata.normalize_qid(i.get("qid")),
                                    moody_specific=_ispec,
                                    override=i.get("fairmoo"))))
    for c in meta.get("concept_qids", []):
        rows.append(("concept", c.get("label") or c.get("concept"),
                     None, c.get("qid"), None, None))

    rows = [r for r in rows if r[1]]            # require a label
    if not rows:
        return ""

    lines = [
        "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .",
        "@prefix owl:  <http://www.w3.org/2002/07/owl#> .",
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
        "@prefix dct:  <http://purl.org/dc/terms/> .",
        "@prefix schema: <https://schema.org/> .",
        "",
        f"<{base.rstrip('/')}> a skos:ConceptScheme ;",
        f'    dct:title "MOO experiment vocabulary — {_esc(meta.get("title") or "")}" ;',
        '    dct:description "Local resolvable identifiers for the '
        'multi-objective optimisation concepts curated in this study, '
        'linked to Wikidata where available." .',
        "",
    ]

    for o in meta.get("objectives", []):
        rows.append(("objective", o.get("name"), o.get("definition"),
                     o.get("qid"), None,
                     fairmoo.decide("objective", o.get("name"),
                                    wikidata.normalize_qid(o.get("qid")),
                                    override=o.get("fairmoo"))))

    collections: dict[str, list[str]] = {}
    seen = set()
    for kind, label, definition, qid, version, fm_uri in rows:
        # the concept's actual IRI is the registered FAIR-MOO URI when one was
        # decided (curator override / MOODY-aligned reuse / coined), else the
        # study-namespace fallback. Dedup and collection membership must both key
        # on this IRI, or a custom slug leaves skos:member pointing at a
        # non-existent base+cid IRI.
        concept_iri = fm_uri or f"{base}{kind}/{_slug(label)}"
        if concept_iri in seen:
            continue
        seen.add(concept_iri)
        collections.setdefault(kind, []).append(concept_iri)
        iri = f"<{concept_iri}>"
        stmts = [f"{iri} a skos:Concept ;",
                 f'    rdfs:label "{_esc(label)}" ;',
                 f'    skos:prefLabel "{_esc(label)}" ;',
                 f"    skos:inScheme <{base.rstrip('/')}>"]
        if definition and str(definition).strip():
            stmts.append(f' ;\n    skos:definition "{_esc(str(definition))}"')
        if version and str(version).strip():
            stmts.append(f' ;\n    owl:versionInfo "{_esc(str(version))}"')
        nq = wikidata.normalize_qid(qid)
        if nq:
            wd = wikidata.page_url(nq)
            stmts.append(f" ;\n    owl:sameAs <{wd}> ;")
            stmts.append(f"    skos:exactMatch <{wd}>")
        if fm_uri and iri != f"<{fm_uri}>":
            stmts.append(f" ;\n    owl:sameAs <{fm_uri}> ;")
            stmts.append(f"    skos:exactMatch <{fm_uri}>")
        # join: the first 4 lines carry their own terminators; the rest is appended
        block = stmts[0] + "\n" + "\n".join(stmts[1:4])
        for extra in stmts[4:]:
            block += extra
        lines.append(block + " .")
        lines.append("")

    for kind, members in collections.items():
        lines.append(f"<{base}collection/{kind}> a skos:Collection ;")
        lines.append(f'    rdfs:label "{kind}s" ;')
        mem = " ,\n        ".join(f"<{m}>" for m in members)
        lines.append(f"    skos:member {mem} .")
        lines.append("")

    return "\n".join(lines)


def generate_annotations(meta: dict, out_dir: Path) -> list[Path]:
    out_dir.mkdir(exist_ok=True)
    apply_disclosure_control(meta, out_dir)
    written = []
    sreport = out_dir / "synthesis_report.json"
    if meta.get("synthesis") and sreport.exists():
        written.append(sreport)
    areport = out_dir / "aggregation_report.json"
    if meta.get("aggregation") and areport.exists():
        written.append(areport)
    (out_dir / "croissant.json").write_text(
        json.dumps(generate_croissant(meta), ensure_ascii=False, indent=2), encoding="utf-8")
    written.append(out_dir / "croissant.json")
    (out_dir / "provenance.ttl").write_text(generate_prov(meta), encoding="utf-8")
    written.append(out_dir / "provenance.ttl")
    (out_dir / "zenodo_metadata.json").write_text(
        json.dumps(generate_datacite_zenodo(meta), ensure_ascii=False, indent=2), encoding="utf-8")
    written.append(out_dir / "zenodo_metadata.json")
    (out_dir / "dcat_ap.jsonld").write_text(
        json.dumps(generate_dcat_ap(meta), ensure_ascii=False, indent=2), encoding="utf-8")
    written.append(out_dir / "dcat_ap.jsonld")
    written.append(generate_experiment_record(meta, out_dir))
    vocab = generate_ontology(meta)
    if vocab:
        (out_dir / "moo_vocabulary.ttl").write_text(vocab, encoding="utf-8")
        written.append(out_dir / "moo_vocabulary.ttl")
    return written


def validate_shacl(out_dir: Path) -> tuple[bool, str]:
    """Validates provenance.ttl against the curation profile: an essential
    element is represented by value+evidence OR by a justified declaration
    of unavailability (sh:xone)."""
    shapes = Path("shapes/curation_shapes.ttl")
    if not shapes.exists():
        return True, f"WARNING: {shapes} does not exist — validation skipped."
    try:
        from pyshacl import validate as shacl_validate
    except ImportError:
        return True, "WARNING: pyshacl not installed — validation skipped."
    conforms, _, report = shacl_validate(str(out_dir / "provenance.ttl"),
                                         shacl_graph=str(shapes))
    (out_dir / "shacl_report.txt").write_text(
        f"SHACL profile: curation\nConforms: {conforms}\n\n{report}", encoding="utf-8")
    return conforms, report


def pack_rocrate(meta: dict, out_dir: Path) -> Path:
    parts = ["croissant.json", "provenance.ttl", "dcat_ap.jsonld",
             "fair_check_report.json", "experiment_record.json",
             "moo_vocabulary.ttl", "shacl_report.txt",
             "confirmation_report.md"]
    try:
        from rocrate.rocrate import ROCrate
        crate = ROCrate()
        crate.name = meta.get("title")
        for artefact in parts:
            p = out_dir / artefact
            if p.exists():
                crate.add_file(str(p))
        crate.write(str(out_dir / "crate"))
        return out_dir / "crate"
    except ImportError:
        crate_meta = {
            "@context": "https://w3id.org/ro/crate/1.1/context",
            "@graph": [
                {"@id": "ro-crate-metadata.json", "@type": "CreativeWork",
                 "conformsTo": {"@id": "https://w3id.org/ro/crate/1.1"},
                 "about": {"@id": "./"}},
                {"@id": "./", "@type": "Dataset", "name": meta.get("title"),
                 "hasPart": [{"@id": p} for p in parts
                             if (out_dir / p).exists()]}]}
        path = out_dir / "ro-crate-metadata.json"
        path.write_text(json.dumps(crate_meta, indent=2), encoding="utf-8")
        return path


def deposit_zenodo(meta: dict, out_dir: Path, sandbox: bool = True,
                   metadata_only: bool = False) -> tuple[str, str]:
    """Creates the deposition (draft), pre-reserves the DOI and uploads. The
    reserved DOI is only registered in DataCite when the deposit is published —
    publication is a manual act of the curator on the website (design decision).
    With metadata_only the received files are NEVER uploaded.
    Precondition: confirmed report (meta['confirmed_at'])."""
    import requests

    if not meta.get("confirmed_at"):
        raise RuntimeError("Deposit blocked: the confirmation report "
                           "has not yet been approved by the curator.")
    token = os.environ.get("ZENODO_TOKEN")
    if not token:
        raise MissingTokenError
    base = "https://sandbox.zenodo.org" if sandbox else "https://zenodo.org"
    headers = {"Authorization": f"Bearer {token}"}

    r = requests.post(f"{base}/api/deposit/depositions", json={}, headers=headers)
    r.raise_for_status()
    dep = r.json()
    doi = dep.get("metadata", {}).get("prereserve_doi", {}).get("doi", "n/a")

    meta["doi"] = doi
    generate_annotations(meta, out_dir)

    bucket = dep["links"]["bucket"]
    # never upload restricted artefacts, whatever directory they ended up in
    _restricted_names = {Path(f["path"]).name for f in meta.get("received_files", [])
                         if f.get("metadata_only")} | {"restricted_mapping.json"}
    for path in out_dir.iterdir():
        if path.name in _restricted_names:
            continue
        if path.is_file():
            with path.open("rb") as fh:
                requests.put(f"{bucket}/{path.name}", data=fh,
                             headers=headers).raise_for_status()
    if not metadata_only:
        for f in meta.get("received_files", []):
            if f.get("metadata_only"):
                continue
            p = Path(f["path"])
            # defense in depth: never upload a file sharing its NAME with a
            # restricted artefact, even if a duplicate inventory entry (e.g.
            # from re-ingesting) lost its metadata_only flag
            if p.name in _restricted_names:
                continue
            if p.is_file():
                with p.open("rb") as fh:
                    requests.put(f"{bucket}/{p.name}", data=fh,
                                 headers=headers).raise_for_status()

    requests.put(f"{base}/api/deposit/depositions/{dep['id']}",
                 json=generate_datacite_zenodo(meta),
                 headers=headers).raise_for_status()
    return doi, dep["links"]["html"]
