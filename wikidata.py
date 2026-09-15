"""
wikidata.py — support for Wikidata QIDs as semantic PIDs.

Role in the solution: the DOI remains the PID of the deposited object (Zenodo);
QIDs semantically anchor the CONCEPTS (algorithm, indicator, software, domain,
benchmark, people/institutions), propagating as:
  · Croissant  → sameAs / about
  · PROV/RDF   → owl:sameAs wd:Qxxx
  · Zenodo     → related_identifiers (relation: references)

The lookup uses the public API's wbsearchentities action and degrades
gracefully without network (returns an empty list + error message).
"""

import re

WD_ENTITY = "http://www.wikidata.org/entity/"      # namespace RDF (owl:sameAs)
WD_PAGE = "https://www.wikidata.org/wiki/"          # human-readable page
API = "https://www.wikidata.org/w/api.php"

_QID_RE = re.compile(r"(Q\d+)$")


def normalize_qid(value: str | None) -> str | None:
    """Accepts 'Q42', 'q42', an entity URL or a page URL; returns 'Q42' or None.
    Never invents: if there is no valid Q-number, returns None."""
    if not value:
        return None
    match = _QID_RE.search(str(value).strip().rstrip("/").upper()
                           .replace("HTTPS://WWW.WIKIDATA.ORG/WIKI/", "")
                           .replace("HTTP://WWW.WIKIDATA.ORG/ENTITY/", ""))
    return match.group(1) if match else None


def entity_uri(qid: str) -> str:
    """RDF URI of the entity (for owl:sameAs)."""
    return WD_ENTITY + qid


def page_url(qid: str) -> str:
    """Human-readable URL (for schema.org sameAs and related_identifiers)."""
    return WD_PAGE + qid


def search(term: str, language: str = "en", limit: int = 5) -> tuple[list[dict], str | None]:
    """Searches entities (wbsearchentities). Returns ([results], error|None);
    each result: {qid, label, description, url}."""
    if not term.strip():
        return [], None
    try:
        import requests
        r = requests.get(API, params={
            "action": "wbsearchentities", "search": term, "language": language,
            "uselang": language, "format": "json", "limit": limit, "type": "item",
        }, timeout=10, headers={"User-Agent": "fair-moo-annotator/0.1 (mockup)"})
        r.raise_for_status()
        return [
            {"qid": e["id"], "label": e.get("label", ""),
             "description": e.get("description", ""), "url": page_url(e["id"])}
            for e in r.json().get("search", [])
        ], None
    except Exception as exc:  # network unavailable, timeout, etc.
        return [], f"Wikidata search unavailable ({type(exc).__name__}). " \
                   "You can paste the QID manually."
