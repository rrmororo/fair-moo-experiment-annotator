"""
fairmoo.py -- FAIR-MOO persistent URIs (https://w3id.org/fair-moo/).

Implements the identifier-selection policy of the framework as code
(the decision tree in the dissertation, Fig. "id_policy"):

    domain concept
      |- adequate specialised MOO-ontology term (MOODY)?  -> reuse it
      |- else: adequate Wikidata QID supplied?            -> reuse Wikidata
      |- else:                                            -> mint FAIR-MOO URI

FAIR-MOO URIs are registered under the W3C Permanent Identifier Community
Group's w3id.org redirection service and resolve to the project's public
GitHub repository. The minter only builds URIs of the registered patterns
  https://w3id.org/fair-moo/<kind>/<slug>
with kind in {objective, problem, algorithm, indicator}. Slugs are
deterministic: the parenthesised acronym of a name when present (e.g.
"Room Changes for Consecutive Classes (RCCC)" -> "rccc"), otherwise the
lower-cased, hyphenated name (e.g. "NSGA-III" -> "nsga-iii"). A curator-
supplied slug or full URI always takes precedence over the derived one.
"""

import re

BASE = "https://w3id.org/fair-moo/"
KINDS = ("objective", "problem", "algorithm", "indicator")

_ACRONYM = re.compile(r"\(([A-Za-z][A-Za-z0-9-]{1,15})\)\s*$")
_NONWORD = re.compile(r"[^a-z0-9]+")


def slugify(name: str | None) -> str | None:
    """Deterministic slug: parenthesised acronym if present, else the
    hyphenated lower-cased name."""
    if not name or not str(name).strip():
        return None
    s = str(name).strip()
    m = _ACRONYM.search(s)
    if m:
        s = m.group(1)
    s = _NONWORD.sub("-", s.lower()).strip("-")
    return s or None


def uri(kind: str, slug_or_uri: str | None) -> str | None:
    """Build (or pass through) a FAIR-MOO URI of a registered pattern."""
    if not slug_or_uri or not str(slug_or_uri).strip():
        return None
    v = str(slug_or_uri).strip()
    if v.startswith("http"):
        return v if v.startswith(BASE) else None
    if kind not in KINDS:
        return None
    return f"{BASE}{kind}/{v.lstrip('/')}"


def decide(kind: str, name: str | None, qid: str | None,
           moody_specific: bool = False, override: str | None = None
           ) -> str | None:
    """Apply the selection policy; returns a FAIR-MOO URI or None.

    None means an established identifier is reused instead (MOODY term or
    Wikidata QID), per the policy: mint ONLY when neither is adequate.
    A curator-supplied `override` (slug or full URI) short-circuits the
    tree -- explicit curation wins over the derived default."""
    if override and str(override).strip():
        return uri(kind, override)
    if moody_specific:          # adequate specialised ontology term -> reuse
        return None
    if qid and str(qid).strip():  # adequate Wikidata QID supplied -> reuse
        return None
    return uri(kind, slugify(name))
