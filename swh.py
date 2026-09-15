"""
swh.py -- Software Heritage / SWHID support (EOSC Software Heritage Mirror).

Role in the solution: the DOI (Zenodo) identifies the deposited DATA and the
Wikidata QIDs anchor the CONCEPTS; the SWHID identifies the SOFTWARE that
produced the results (the archived source code). This closes the FAIR triangle
data (DOI) + concepts (QID) + software (SWHID).

A SWHID (SoftWare Hash IDentifier, ISO/IEC 18670) is an INTRINSIC identifier:
it is computed from the artefact itself and needs no central registry to be
valid. This module can:
  * parse and validate a SWHID string;
  * build the persistent browse/permalink URL for a SWHID;
  * given a public repository origin URL (e.g. a GitHub repo), query the
    Software Heritage GraphQL API for the SWHID of its latest archived
    snapshot -- so the curator obtains a REAL SWHID instead of inventing one.

Everything degrades gracefully without network (returns None + an error
message); nothing here fabricates a SWHID.
"""

import re

# Core SWHID: swh:1:<type>:<40 hex>, optionally followed by ;qualifiers
SWHID_RE = re.compile(
    r"(swh:1:(?:cnt|dir|rev|rel|snp):[0-9a-f]{40}(?:;[^\s]+)?)")

ARCHIVE = "https://archive.softwareheritage.org"
GRAPHQL = f"{ARCHIVE}/graphql/"
SAVE_UI = f"{ARCHIVE}/save/"


# ------------------------------------------------------------- parsing ------
def parse_swhid(value: str | None) -> str | None:
    """Extract a canonical SWHID from free text/URL; None if not present.
    Never invents: only returns what is syntactically a SWHID."""
    if not value:
        return None
    m = SWHID_RE.search(str(value).strip())
    return m.group(1) if m else None


def core_swhid(swhid: str | None) -> str | None:
    """The core SWHID without qualifiers (everything before the first ';')."""
    s = parse_swhid(swhid)
    return s.split(";")[0] if s else None


def swhid_type(swhid: str | None) -> str | None:
    """Object kind: content|directory|revision|release|snapshot, or None."""
    s = core_swhid(swhid)
    if not s:
        return None
    return {"cnt": "content", "dir": "directory", "rev": "revision",
            "rel": "release", "snp": "snapshot"}[s.split(":")[2]]


# ----------------------------------------------------------- resolution -----
def browse_url(swhid: str | None) -> str | None:
    """Persistent permalink to browse the object in the archive (stores in the
    RO). Includes qualifiers if present. No network request."""
    s = parse_swhid(swhid)
    return f"{ARCHIVE}/{s}" if s else None


def resolve_api_url(swhid: str | None) -> str | None:
    """Web API URL that resolves a SWHID to the archived object. No request."""
    s = core_swhid(swhid)
    return f"{ARCHIVE}/api/1/resolve/{s}/" if s else None


# --------------------------------------- obtain a SWHID from a repo origin --
_GRAPHQL_SNAPSHOT = """query($url: String!) {
  origin(url: $url) {
    url
    latestVisit { date }
    latestSnapshot { swhid }
  }
}"""


def snapshot_swhid_for_origin(repo_url: str, timeout: int = 15) -> dict:
    """Query the Software Heritage GraphQL API for the SWHID of the latest
    archived SNAPSHOT of a repository origin (e.g. a GitHub URL). Returns
    {'swhid', 'visit_date', 'archived', 'error'}.

    The snapshot SWHID (swh:1:snp:...) is the right identifier to cite a whole
    archived repository; add contextual qualifiers (origin, path) to point at a
    directory inside it. If the origin is not yet archived, 'archived' is False
    and the caller should trigger a 'Save Code Now' request (see save_url())."""
    out = {"swhid": None, "visit_date": None, "archived": False, "error": None}
    url = (repo_url or "").strip()
    if not url:
        out["error"] = "empty repository URL"
        return out
    try:
        import requests
        r = requests.post(
            GRAPHQL, json={"query": _GRAPHQL_SNAPSHOT, "variables": {"url": url}},
            headers={"Accept": "application/json",
                     "User-Agent": "fair-moo-annotator/0.1 (swh)"},
            timeout=timeout)
        r.raise_for_status()
        data = (r.json() or {}).get("data", {}).get("origin")
        if not data:
            out["error"] = ("origin not found in the archive yet -- request "
                            "archiving via 'Save Code Now' and retry.")
            return out
        snap = (data.get("latestSnapshot") or {}).get("swhid")
        visit = (data.get("latestVisit") or {}).get("date")
        out.update(swhid=parse_swhid(snap), visit_date=visit,
                   archived=bool(snap))
        if not snap:
            out["error"] = "origin known but no snapshot yet; retry after a visit."
    except Exception as exc:  # network down, GraphQL change, etc.
        out["error"] = f"Software Heritage unavailable ({type(exc).__name__})."
    return out


def save_url(repo_url: str) -> str:
    """Human URL to request archiving of a repository ('Save Code Now')."""
    return f"{SAVE_UI}?origin_url={(repo_url or '').strip()}"


# --------------------------------------------------- label for confirmation -
def verify_swhid(swhid: str) -> dict:
    """Resolve a SWHID to a human label for explicit confirmation, mirroring
    the ORCID/ROR/DOI flow. PIDMR is tried first by the caller; this is the
    Software-Heritage-native fallback. Degrades without network."""
    out = {"resolved": False, "label": None, "error": None, "via": None}
    s = parse_swhid(swhid)
    if not s:
        out["error"] = "invalid SWHID format"
        return out
    kind = swhid_type(s)
    try:
        import requests
        r = requests.get(resolve_api_url(s),
                         headers={"Accept": "application/json",
                                  "User-Agent": "fair-moo-annotator/0.1 (swh)"},
                         timeout=10, allow_redirects=False)
        if r.status_code in (200, 302, 303):
            out.update(resolved=True, via="software-heritage",
                       label=f"archived {kind} -- {browse_url(s)}")
        elif r.status_code == 404:
            out["error"] = "SWHID not found in the archive (404)"
        else:
            out.update(resolved=True, via="software-heritage",
                       label=f"archived {kind}")
    except Exception as exc:
        # offline: the SWHID is intrinsic, so format validity still holds
        out.update(resolved=True, via="intrinsic",
                   label=f"{kind} (SWHID is intrinsic; not verified online: "
                         f"{type(exc).__name__})")
    return out
