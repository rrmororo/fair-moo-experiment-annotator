"""
pid_registry.py — parsing, API verification and confirmation of PIDs.

Required flow: the PIDs entered by the curator (ORCID, ROR, DOI) are
1) parsed/normalised, 2) queried against the API of the registry where they
are allocated (pub.orcid.org, api.ror.org, api.crossref.org), 3) presented to
the user with the resolved label (person's name / organisation / work title)
for explicit CONFIRMATION before deposit. A PID with a valid format is never
assumed to point to the right entity — only the user can confirm.

Creation of PIDs for concepts (algorithms, problems, indicators):
Wikidata is the practicable route — any user can create items, which then
propagate to aggregator graphs. The OpenAIRE Graph AGGREGATES entities from
sources (repositories, CRIS, registries); it is not a service where the end
user mints concept PIDs directly — the route is to create on Wikidata (or
deposit on Zenodo, which OpenAIRE harvests) and let the graph harvest. For
batch creation, a QuickStatements file (Wikidata's official bulk-editing
format) is generated with the proposed items — the greatest ambition are the
algorithms, which rarely have an item of their own.
"""

import re

ORCID_RE = re.compile(r"(\d{4}-\d{4}-\d{4}-\d{3}[\dX])")
ROR_RE = re.compile(r"(0[a-hj-km-np-tv-z0-9]{6}\d{2})")   # 9 chars, no i/l/o/u
DOI_RE = re.compile(r"(10\.\d{4,9}/\S+)")
SWHID_RE = re.compile(r"(swh:1:(?:cnt|dir|rev|rel|snp):[0-9a-f]{40}(?:;\S+)?)")

APIS = {
    "orcid": "https://pub.orcid.org/v3.0/{id}",
    "ror": "https://api.ror.org/organizations/{id}",
    "doi": "https://api.crossref.org/works/{id}",
}


# ------------------------------------------------------------- parsing ------
def parse_pid(kind: str, value: str | None) -> str | None:
    """Extracts the canonical identifier from free text/URL. None if invalid."""
    if not value:
        return None
    value = str(value).strip()
    regex = {"orcid": ORCID_RE, "ror": ROR_RE, "doi": DOI_RE,
             "swhid": SWHID_RE}[kind]
    m = regex.search(value)
    if not m:
        return None
    return m.group(1) if kind == "swhid" else m.group(1).rstrip(".,;)")


def collect_pids(meta: dict) -> list[dict]:
    """All PIDs present in the inputs: [{kind, raw, normalized, where}]."""
    found = []

    def _add(kind, raw, where):
        if raw and str(raw).strip():
            found.append({"kind": kind, "raw": str(raw).strip(),
                          "normalized": parse_pid(kind, raw), "where": where})

    for role in ("creators", "curators"):
        for i, person in enumerate(meta.get(role, [])):
            _add("orcid", person.get("orcid"), f"{role}[{i}] {person.get('name', '')}")
            _add("ror", person.get("ror"), f"{role}[{i}] affiliation")
    _add("doi", meta.get("replicated_work"), "replicated work")
    _add("doi", meta.get("source_document"), "source document")
    _add("doi", meta.get("instance_provenance"), "instance provenance")
    _add("swhid", meta.get("code_swhid"), "software source code (Software Heritage)")
    # drop unmatched doi entries (URLs that are not DOIs are not an error)
    return [p for p in found
            if p["normalized"] or p["kind"] in ("orcid", "ror", "swhid")]


# --------------------------------------------------------- verification -----
def verify_pid(kind: str, normalized: str) -> dict:
    """Verifies a PID and returns a resolved label for the user to confirm.
    Maximum integration with the EOSC PID Meta Resolver: the PIDMR is the
    FIRST route (detects the type, stores the resolution URL and tries the
    metadata/label); only if the PIDMR returns no label is the provider API
    (Crossref/ORCID/ROR) queried for a reliable human label. Degrades
    without network."""
    out = {"resolved": False, "label": None, "error": None,
           "pidmr_url": None, "via": None}
    # 1) PIDMR — resolver of record
    try:
        import pidmr
        info = pidmr.fetch_metadata(normalized)
        out["pidmr_url"] = info.get("pidmr_url")
        if info.get("label"):
            out.update(resolved=True, label=info["label"], via="pidmr")
            return out
        if info.get("error"):
            out["error"] = info["error"]
    except Exception:
        pass
    # 2a) SWHID: Software-Heritage-native resolution (intrinsic identifier)
    if kind == "swhid":
        try:
            import swh
            sh = swh.verify_swhid(normalized)
            if sh.get("resolved"):
                out.update(resolved=True, label=sh["label"],
                           via=sh.get("via", "software-heritage"))
            else:
                out["error"] = sh.get("error")
        except Exception as exc:
            out["error"] = f"SWHID check failed ({type(exc).__name__})"
        return out
    # 2) fallback: provider API for a readable label
    try:
        import requests
        headers = {"Accept": "application/json",
                   "User-Agent": "fair-moo-annotator/0.1 (mockup)"}
        r = requests.get(APIS[kind].format(id=normalized),
                         headers=headers, timeout=10)
        if r.status_code == 404:
            out["error"] = "not found in the registry (404)"
            return out
        r.raise_for_status()
        data = r.json()
        if kind == "orcid":
            name = data.get("person", {}).get("name", {}) or {}
            given = (name.get("given-names") or {}).get("value", "")
            family = (name.get("family-name") or {}).get("value", "")
            out["label"] = f"{given} {family}".strip() or "(name not public)"
        elif kind == "ror":
            out["label"] = data.get("name")
        elif kind == "doi":
            titles = data.get("message", {}).get("title") or []
            out["label"] = titles[0] if titles else "(no title in Crossref)"
        out["resolved"] = True
        out["via"] = "provider"
        out["error"] = None
    except Exception as exc:
        if not out.get("label"):
            out["error"] = (f"API unavailable ({type(exc).__name__}) — "
                            "confirm manually at the source.")
    return out


def verify_all(meta: dict) -> list[dict]:
    """Parse + verification of all PIDs; adds format status.
    The SAME identifier used in several contexts (e.g. the same institution's
    ROR in the creator's and in the curator's affiliation) is deduplicated and
    confirmed only ONCE, with the contexts merged into "where"."""
    merged: dict[tuple, dict] = {}
    for pid in collect_pids(meta):
        ident = (pid["kind"], pid["normalized"] or pid["raw"])
        if ident in merged:
            merged[ident]["where"] += f" · {pid['where']}"
            continue
        entry = dict(pid)
        if not pid["normalized"]:
            entry.update({"resolved": False, "label": None,
                          "error": "invalid format — not parseable"})
        else:
            entry.update(verify_pid(pid["kind"], pid["normalized"]))
        merged[ident] = entry
    return list(merged.values())


# ------------------------------------- creation of concept PIDs -------------
def quickstatements_for_missing_concepts(meta: dict) -> str:
    """Generates QuickStatements (v1) commands to create Wikidata items for
    the concepts WITHOUT a QID — mainly algorithms. The user reviews,
    completes the P31 (instance of) — left unfilled on purpose: use the
    sidebar search to find the right class, e.g. 'optimisation algorithm' —
    and submits at quickstatements.toolforge.org with their own account.
    We never create items automatically: creation is an act reviewed and
    signed by the curator."""
    import wikidata
    lines = ["# QuickStatements v1 — proposed items (review before submitting)",
             "# P31 (instance of) left unfilled: replace Q_FILL_IN",
             ""]
    missing = [row for row in meta.get("concept_qids", [])
               if not wikidata.normalize_qid(row.get("qid"))
               and (row.get("label") or "").strip()]
    for row in missing:
        label = row["label"].strip()
        desc = (row.get("concept") or "multi-objective optimisation concept").strip()
        lines += ["CREATE",
                  f'LAST|Len|"{label}"',
                  f'LAST|Den|"{desc} (multi-objective optimisation)"',
                  'LAST|P31|Q_FILL_IN', ""]
    if not missing:
        lines.append("# (all concepts already have a QID — nothing to create)")
    return "\n".join(lines)


# ---------------------------------------------- search by name --------------
def _norm_name(s: str) -> str:
    """lowercase and accent-free, for tolerant name comparison."""
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def _orcid_search(q: str, rows: int) -> list[dict]:
    """Executes an ORCID expanded-search query and returns raw hit dicts."""
    import requests
    r = requests.get("https://pub.orcid.org/v3.0/expanded-search/",
                     params={"q": q, "rows": rows},
                     headers={"Accept": "application/json"}, timeout=10)
    r.raise_for_status()
    hits = []
    for hit in (r.json().get("expanded-result") or []):
        name = " ".join(x for x in (hit.get("given-names"),
                                    hit.get("family-names")) if x)
        inst = "; ".join(hit.get("institution-name") or [])
        hits.append({"orcid": hit.get("orcid-id", ""), "name": name,
                     "institutions": inst[:120]})
    return hits


def search_orcid(term: str, rows: int = 10) -> tuple[list[dict], str | None]:
    """Searches for PEOPLE by name on ORCID with two-stage precision:
    1) STRUCTURED query (given-names + family-names) for multi-word terms;
       falls back to a plain full-name query if the server returns 5xx
       (the ORCID Solr engine occasionally rejects wildcard queries for
       short tokens with an Internal Server Error);
    2) ARRIVAL FILTER: only candidates whose visible name contains ALL
       searched words (accent- and case-insensitive) are returned."""
    import requests
    if not term or not term.strip():
        return [], "Empty search term."
    tokens = term.strip().split()
    wanted = [_norm_name(tok) for tok in tokens]

    hits = []
    if len(tokens) >= 2:
        structured_q = f'given-names:{tokens[0]}* AND family-names:{tokens[-1]}*'
        try:
            hits = _orcid_search(structured_q, rows)
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code >= 500:
                # server-side failure on the structured query — fall back to
                # plain text search, which is handled more robustly by the API
                try:
                    hits = _orcid_search(term.strip(), rows)
                except Exception as exc2:
                    return [], f"ORCID unavailable: {exc2}"
            else:
                return [], f"ORCID unavailable: {exc}"
        except Exception as exc:
            return [], f"ORCID unavailable: {exc}"
    else:
        try:
            hits = _orcid_search(tokens[0], rows)
        except Exception as exc:
            return [], f"ORCID unavailable: {exc}"

    filtered = [h for h in hits
                if all(w in _norm_name(h["name"]) for w in wanted)]
    return filtered[:5], None


def search_ror(term: str, rows: int = 5) -> tuple[list[dict], str | None]:
    """Searches ORGANISATIONS by name on the public ROR API.
    Returns candidates {ror, name, country} for explicit confirmation."""
    import requests
    if not term or not term.strip():
        return [], "Empty search term."
    try:
        r = requests.get("https://api.ror.org/v2/organizations",
                         params={"query": term.strip()}, timeout=10)
        r.raise_for_status()
        payload = r.json()
    except Exception as exc:
        return [], f"ROR unavailable: {exc}"
    out = []
    for item in (payload.get("items") or [])[:rows]:
        if "names" in item:                                    # schema v2
            names = item.get("names") or []
            name = next((n.get("value") for n in names
                         if "ror_display" in (n.get("types") or [])),
                        names[0].get("value") if names else "")
            locs = item.get("locations") or []
            country = ((locs[0].get("geonames_details") or {})
                       .get("country_name", "")) if locs else ""
        else:                                                  # schema v1
            name = item.get("name", "")
            country = (item.get("country") or {}).get("country_name", "")
        out.append({"ror": str(item.get("id", "")).replace("https://ror.org/", ""),
                    "name": name, "country": country})
    wanted = [_norm_name(tok) for tok in term.strip().split()]
    filtered = [o for o in out
                if all(w in _norm_name(o["name"]) for w in wanted)]
    return (filtered or out)[:rows], None
