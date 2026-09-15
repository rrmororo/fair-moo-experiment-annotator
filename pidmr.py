"""
pidmr.py — integration with the EOSC PID Meta Resolver (PIDMR), a core
FAIRCORE4EOSC component (api.pidmr.argo.grnet.gr).

The PIDMR is a meta-resolver: it takes a PID string, detects the provider/type
and resolves it to a landing page, metadata or object. It does NOT search by
name nor mint PIDs — name search (ORCID/ROR/Wikidata) and item creation
(Wikidata) remain in their respective modules. Wikidata is one of the ~50
supported providers, which gives EOSC-native coverage to the concepts
(algorithms/problems/indicators).

Role in the app ("maximum" integration): resolver of record. In the
verification step the PIDMR is the FIRST route — it detects the type and tries
to pull the label/metadata; only if it fails do we fall back to the provider
APIs. The PIDMR resolution URL is stored on each PID, making "resolvable via
the EOSC PID Meta Resolver" true and verifiable.

The (Quarkus) endpoints are kept in constants — CONFIRM against
https://api.pidmr.argo.grnet.gr/swagger-ui. Everything degrades gracefully
without network.
"""

import re
from urllib.parse import quote

API_BASE = "https://api.pidmr.argo.grnet.gr"
PROVIDERS_PATH = "/v1/providers"              # lists providers + regexes (confirm)
RESOLVE_PATH = "/v1/metaresolvers/resolve"    # ?identifier=&pidMode= (confirm)
UI_BASE = "https://pidmr.argo.grnet.gr"       # human UI

_TIMEOUT = 10
_HEADERS = {"Accept": "application/json",
            "User-Agent": "fair-moo-annotator/0.1 (pidmr)"}

# minimal local fallback if the PIDMR provider list is not reachable
_LOCAL_PATTERNS = {
    "doi": r"^(doi:)?10\.\d{4,9}/\S+$",
    "orcid": r"^(https?://orcid\.org/)?\d{4}-\d{4}-\d{4}-\d{3}[\dX]$",
    "ror": r"^(https?://ror\.org/)?0[a-hj-km-np-tv-z0-9]{6}\d{2}$",
    "wikidata": r"^(https?://www\.wikidata\.org/(wiki|entity)/)?Q\d+$",
    "swhid": r"^swh:1:(cnt|dir|rev|rel|snp):[0-9a-f]{40}(;\S+)?$",
    "handle": r"^(hdl:)?\d+(\.\d+)*/\S+$",
    "arxiv": r"^(arxiv:)?\d{4}\.\d{4,5}(v\d+)?$",
}

_providers_cache = None


def _get(path, params=None):
    import requests
    r = requests.get(API_BASE + path, params=params or {},
                     headers=_HEADERS, timeout=_TIMEOUT, allow_redirects=True)
    r.raise_for_status()
    return r


def providers(force=False):
    """PIDMR provider list (in-memory cache). [] if unavailable.
    Each item is normalised to {'type', 'name', 'regexes': [...]}."""
    global _providers_cache
    if _providers_cache is not None and not force:
        return _providers_cache
    out = []
    try:
        data = _get(PROVIDERS_PATH, {"size": 100}).json()
        items = data.get("content", data) if isinstance(data, dict) else data
        for p in items or []:
            regexes = p.get("regexes") or p.get("regex") or p.get("patterns") or []
            if isinstance(regexes, str):
                regexes = [regexes]
            out.append({"type": p.get("type") or p.get("id") or p.get("name"),
                        "name": p.get("name") or p.get("type"),
                        "regexes": [x for x in regexes if x]})
    except Exception:
        out = []
    _providers_cache = out
    return out


def identify(pid):
    """Detects a PID's type/provider via the PIDMR provider list; degrades
    to local patterns. Returns {'type','name'} or None."""
    pid = (pid or "").strip()
    if not pid:
        return None
    for p in providers():
        for rx in p["regexes"]:
            try:
                if re.search(rx, pid):
                    return {"type": p["type"], "name": p["name"]}
            except re.error:
                continue
    for kind, rx in _LOCAL_PATTERNS.items():
        if re.search(rx, pid, flags=re.IGNORECASE):
            return {"type": kind, "name": kind}
    return None


def resolve_url(pid, mode="landingpage"):
    """PIDMR resolution URL (stable, to store in the RO). mode ∈
    {landingpage, metadata, resource}. Makes no network request."""
    return (f"{API_BASE}{RESOLVE_PATH}"
            f"?identifier={quote((pid or '').strip(), safe='')}&pidMode={mode}")


def resolve(pid, mode="landingpage"):
    """Resolves via PIDMR and returns the final URL (after redirects).
    Without network, returns the constructed resolve_url."""
    try:
        r = _get(RESOLVE_PATH, {"identifier": (pid or "").strip(), "pidMode": mode})
        return r.url or resolve_url(pid, mode)
    except Exception:
        return resolve_url(pid, mode)


def _extract_label(obj):
    """Looks for a human label in heterogeneous metadata responses."""
    if isinstance(obj, dict):
        for k in ("title", "name", "label", "titles", "fullName", "prefLabel"):
            v = obj.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
            if isinstance(v, list) and v and isinstance(v[0], (str, dict)):
                return _extract_label(v[0]) if isinstance(v[0], dict) else v[0]
        for v in obj.values():                       # depth-first search
            lab = _extract_label(v)
            if lab:
                return lab
    return None


def fetch_metadata(pid):
    """Tries to fetch metadata ('metadata' mode) and extract a label. Returns
    {'label', 'type', 'pidmr_url', 'error'}; label/error as available.
    Metadata coverage varies by provider — if empty, the caller falls back
    to the provider APIs."""
    out = {"label": None, "type": None, "pidmr_url": resolve_url(pid, "landingpage"),
           "error": None}
    ident = identify(pid)
    if ident:
        out["type"] = ident["type"]
    try:
        r = _get(RESOLVE_PATH, {"identifier": (pid or "").strip(),
                                "pidMode": "metadata"})
        try:
            out["label"] = _extract_label(r.json())
        except ValueError:
            out["label"] = None                      # non-JSON response
    except Exception as exc:
        out["error"] = f"PIDMR unavailable ({type(exc).__name__})"
    return out
