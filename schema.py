"""
schema.py — Metadata profile for RETROSPECTIVE CURATION (single mode).

The app is exclusively for retrospective FAIRification: the curator annotates
the data and metadata of an existing MOO experiment (case: Batista's
dissertation), from the received files and the documentary sources. There is
no prospective mode and no observer — no optimisation code is executed.

Single source of truth: the form and the serializers are generated from here.
Each field carries an obligation (M/C/R/O) and per-vocabulary mappings.
Obligations: M = mandatory; C = conditional (the condition goes in the help);
R = recommended; O = optional. In curation, an M/C field is satisfied by
a documented value OR a declaration of "not recorded in the source" with
justification — the declaration satisfies the procedure; it does not make the
information complete (the limitation continues to be reported in the FAIR checks).
"""

LICENSES = ["CC-BY-4.0", "CC-BY-SA-4.0", "CC0-1.0", "MIT", "Apache-2.0"]
ACCESS_RIGHTS = ["open", "embargoed", "restricted", "closed"]

# Quasi-identifying columns of institutional datasets that must be pseudonymised
# before deposit (R6). Single source of truth for the de-identification step and
# the FAIR check. Column name -> surrogate prefix.
QUASI_IDENTIFIERS = {
    "Curso": "CRS",               # programme/course identifier
    "Unidade de execução": "UC",
    "Turno": "TRN",
    "Turma": "TRM",
}

FORM_BLOCKS = [
    {
        "id": "bc",
        "title": "C · Curation",
        "help": ("Who annotates, based on what and with what authorization. Generates the "
                 "curation activity (PROV) and the DataCite contributors."),
        "fields": [
            {
                "key": "curators", "label": "Curators", "obligation": "M",
                "widget": "table",
                "columns": {"name": "Name", "orcid": "ORCID",
                            "ror": "ROR (affiliation)", "affiliation": "Affiliation"},
                "help": "Who performs the annotation (you). Goes to contributor/DataCurator.",
                "maps": {"datacite": "contributor (DataCurator)",
                         "prov": "prov:Agent (curation)"},
            },
            {
                "key": "source_document",
                "label": "Source document (citation or DOI/handle of the dissertation)",
                "obligation": "M", "widget": "text",
                "help": "The original author's dissertation. prov:hadPrimarySource of all extracted metadata.",
                "maps": {"datacite": "relatedIdentifier (IsDerivedFrom)",
                         "prov": "prov:hadPrimarySource"},
            },
            {
                "key": "authorization_status",
                "label": "Authorization to redistribute the data", "obligation": "M",
                "widget": "select",
                "options": ["authorized by the author", "pending",
                            "no authorization — metadata-only deposit"],
                "help": "Conditions the access_right and whether files are uploaded to Zenodo.",
                "maps": {"datacite": "rights (access)"},
            },
            {
                "key": "curation_notes", "label": "Curation notes",
                "obligation": "O", "widget": "textarea",
                "help": "Decisions, doubts and limitations of the annotation process.",
                "maps": {"datacite": "description (Methods)"},
            },
        ],
    },
    {
        "id": "b1",
        "title": "1 · Description and citation",
        "help": "Metadata that make the deposit citable and searchable (Findability).",
        "fields": [
            {"key": "title", "label": "Title of the curated record", "obligation": "M",
             "widget": "text",
             "help": "E.g.: 'Curated MOO timetabling experiment — Batista (2025)'",
             "maps": {"croissant": "name", "datacite": "title", "dcat": "dct:title"}},
            {"key": "description", "label": "Description / abstract", "obligation": "M",
             "widget": "textarea",
             "maps": {"croissant": "description", "datacite": "description",
                      "dcat": "dct:description"}},
            {"key": "creators", "label": "Original creators (authors of the experiment)",
             "obligation": "M", "widget": "table",
             "columns": {"name": "Name", "orcid": "ORCID",
                         "ror": "ROR (affiliation)", "affiliation": "Affiliation"},
             "help": "The author(s) of the original experiment. Not the curator.",
             "maps": {"croissant": "creator", "datacite": "creator",
                      "prov": "prov:Agent (original experiment)"}},
            {"key": "license", "label": "License", "obligation": "M",
             "widget": "select", "options": LICENSES,
             "maps": {"croissant": "license", "datacite": "rights",
                      "dcat": "dct:license"}},
            {"key": "keywords", "label": "Keywords", "obligation": "R",
             "widget": "tags",
             "help": "E.g.: multi-objective optimisation, university timetabling, FAIR, curation",
             "maps": {"croissant": "keywords", "datacite": "subject",
                      "dcat": "dcat:keyword"}},
            {"key": "version", "label": "Version", "obligation": "M",
             "widget": "text", "default": "1.0.0",
             "maps": {"croissant": "version", "datacite": "version"}},
            {"key": "language", "label": "Language", "obligation": "O",
             "widget": "select", "options": ["en", "pt"],
             "maps": {"croissant": "inLanguage", "datacite": "language"}},
        ],
    },
    {
        "id": "b2",
        "title": "2 · Problem instances",
        "help": ("The datasets used in the original experiment: institutional "
                 "(ISCTE) and benchmark instances (ITC2007, .ctt format)."),
        "fields": [
            {"key": "instance_name", "label": "Main instance(s)",
             "obligation": "M", "widget": "text",
             "help": "E.g.: 'ISCTE institutional datasets + ITC2007 Comp02 (.ctt)'",
             "maps": {"croissant": "FileObject.name", "moody": "ProblemInstance",
                      "prov": "prov:Entity"}},
            {"key": "instance_provenance",
             "label": "Provenance of the instances (URL/DOI/source)",
             "obligation": "M", "widget": "text",
             "help": "Where the datasets come from (ITC2007: competition website; ISCTE: institutional origin).",
             "maps": {"croissant": "sameAs", "datacite": "relatedIdentifier",
                      "prov": "prov:wasDerivedFrom"}},
            {"key": "instance_qid", "label": "Wikidata QID of the benchmark",
             "obligation": "O", "widget": "text",
             "help": "If an item exists for the ITC2007/benchmark. Use the sidebar search; existing QIDs only.",
             "maps": {"croissant": "sameAs", "prov": "owl:sameAs",
                      "datacite": "relatedIdentifier (references)"}},
            {"key": "instance_dims",
             "label": "Dimensions/characterisation (as in the source)", "obligation": "R",
             "widget": "text",
             "maps": {"moody": "ProblemInstance (attributes)"}},
            {"key": "constraints_summary",
             "label": "Hard/soft constraints (as in the source)", "obligation": "R",
             "widget": "textarea",
             "help": "The constraint/objective functions are in the received Java code (CalculateConstraints, ObjetiveFunctionsHard/Soft).",
             "maps": {"moody": "Constraint"}},
        ],
    },
    {
        "id": "b3",
        "title": "3 · MOO formulation",
        "help": "Objectives and model variables, as reported in the source.",
        "fields": [
            {"key": "objectives", "label": "Objectives", "obligation": "M",
             "widget": "table",
             "columns": {"name": "Name", "direction": "Direction (min/max)",
                         "definition": "Definition", "qid": "Wikidata QID",
                         "fairmoo": "FAIR-MOO slug (optional)"},
             "maps": {"croissant": "variableMeasured", "moody": "Objective"}},
            {"key": "decision_variables",
             "label": "Decision variables (type, encoding)", "obligation": "M",
             "widget": "textarea",
             "maps": {"moody": "Variable / Encoding"}},
            {"key": "model_constraints",
             "label": "Model constraints", "obligation": "R",
             "widget": "textarea",
             "maps": {"moody": "Constraint"}},
        ],
    },
    {
        "id": "b4",
        "title": "4 · Algorithms, configurations and indicators",
        "help": ("What the source reports about the algorithms and the experimental "
                 "variants. Whatever is not in the source is declared "
                 "'not recorded' — never invented."),
        "fields": [
            {"key": "instance_fairmoo",
             "label": "FAIR-MOO URI/slug of the institutional problem instance",
             "obligation": "R", "widget": "text",
             "help": ("Registered FAIR-MOO problem identifier for the "
                      "institutional instance (e.g. iscte-timetabling -> "
                      "https://w3id.org/fair-moo/problem/iscte-timetabling). "
                      "Minted only because the instance has no external "
                      "identifier; resolves via w3id.org to the public repo."),
             "maps": {"croissant": "sameAs", "prov": "owl:sameAs"}},
            {"key": "benchmark_fairmoo",
             "label": "FAIR-MOO URI/slug of the benchmark instance",
             "obligation": "O", "widget": "text",
             "help": ("Registered FAIR-MOO problem identifier for the public "
                      "benchmark (e.g. itc2007-comp02). The benchmark keeps "
                      "its published DOI as a related identifier; the "
                      "FAIR-MOO URI is its semantic anchor."),
             "maps": {"croissant": "sameAs", "prov": "owl:sameAs"}},
            {"key": "code_repository",
             "label": "Source-code repository (URL)", "obligation": "R", "widget": "text",
             "help": ("Public repository of the software that produced the results "
                      "(e.g. the GitHub repo with the Java/jMetal code and derived "
                      "data). Archived by Software Heritage to mint a SWHID. Never "
                      "publish the raw institutional microdata here."),
             "maps": {"croissant": "isBasedOn", "prov": "prov:used",
                      "datacite": "relatedIdentifier"}},
            {"key": "code_swhid",
             "label": "Software Heritage ID (SWHID) of the code", "obligation": "R",
             "widget": "text",
             "help": ("swh:1:snp:... (or dir/rev) of the archived source code. "
                      "Obtain it with 'Save Code Now' + the GraphQL query, or run "
                      "get_swhid.py on the repository URL. Identifies the SOFTWARE, "
                      "as the DOI identifies the data and the QID the concepts."),
             "maps": {"croissant": "sameAs", "prov": "owl:sameAs",
                      "datacite": "relatedIdentifier (references)"}},
            {"key": "algorithms", "label": "Algorithms studied", "obligation": "M",
             "widget": "table",
             "columns": {"name": "Algorithm", "version": "Software version",
                         "qid": "Wikidata QID",
                         "fairmoo": "FAIR-MOO slug (optional)",
                         "doi": "Software/paper DOI (optional)"},
             "help": ("QIDs via the sidebar search. Software version: the version "
                      "of the implementing software the algorithm was run with "
                      "(e.g. jMetal 6.6), as reported in the source. Optional DOI: "
                      "the resolvable identifier of the implementing software/paper "
                      "(e.g. jMetalPy), which links the algorithm as a node in the "
                      "OpenAIRE Graph."),
             "maps": {"moody": "Algorithm", "prov": "owl:sameAs",
                      "datacite": "relatedIdentifier (references)"}},
            {"key": "configurations", "label": "Experimental configurations",
             "obligation": "M", "widget": "table",
             "columns": {"label": "Label", "description": "Description"},
             "help": ("The compared variants of the experimental setup."),
             "maps": {"moody": "Parameter/Configuration", "prov": "prov:Plan"}},
            {"key": "indicators", "label": "Reported quality indicators",
             "obligation": "M", "widget": "table",
             "columns": {"name": "Indicator", "params": "Parameters (ref. point, normalisation…)",
                         "qid": "Wikidata QID",
                         "fairmoo": "FAIR-MOO slug (optional)"},
             "help": ("As in the QualityIndicatorSummary. C: if hypervolume is "
                      "reported, the reference point is mandatory — if the source does "
                      "not state it, declare 'not recorded'. Every indicator also "
                      "receives a resolvable FAIR-MOO URI as its canonical "
                      "identifier, exactly like the objectives; the Wikidata QID, "
                      "when present, is attached as an equivalence (owl:sameAs). "
                      "Leave the slug blank to mint one from the name — "
                      "MOODY-covered indicators (hypervolume, spread…) reuse "
                      "their MOODY typing while still getting the FAIR-MOO IRI."),
             "maps": {"moody": "Indicator", "croissant": "variableMeasured",
                      "prov": "owl:sameAs"}},
            {"key": "seeds_and_runs",
             "label": "Seeds and number of independent runs (as in the source)",
             "obligation": "C", "widget": "text",
             "help": "C: essential for reproducibility; if absent from the source, mark 'not recorded in the source'.",
             "maps": {"moody": "Parameter", "prov": "prov:Plan"}},
            {"key": "benchmark_comparison",
             "label": "Benchmark comparison (ITC2007 Comp02)", "obligation": "R",
             "widget": "textarea",
             "help": "What the source reports about the comparison with ITC2007 results.",
             "maps": {"croissant": "description"}},
            {"key": "concept_qids", "label": "Other semantic PIDs (Wikidata)",
             "obligation": "R", "widget": "table",
             "columns": {"concept": "Concept", "label": "Label", "qid": "QID",
                         "doi": "Artifact DOI (optional)"},
             "help": ("Software (jMetal), domain (university timetabling), etc. "
                      "Existing QIDs only. Optional DOI: resolvable identifier of "
                      "the artefact (software/benchmark) to link it in the Graph."),
             "maps": {"croissant": "about (sameAs)", "prov": "owl:sameAs",
                      "datacite": "relatedIdentifier (references)"}},
        ],
    },
    {
        "id": "bdp",
        "title": "DP · Data protection & disclosure control",
        "help": ("Ethics/GDPR controls for the institutional dataset. Pseudonymising "
                 "direct identifiers is NOT sufficient: re-identification is possible "
                 "through rare combinations. Choose how the microdata is shared and "
                 "document it."),
        "fields": [
            {"key": "sharing_mode",
             "label": "Sharing mode for the institutional dataset", "obligation": "M",
             "widget": "select",
             "options": ["synthetic — open synthetic stand-in, raw restricted",
                         "restricted — files under controlled access",
                         "metadata-only — files not distributed",
                         "aggregated — only aggregated/derived data published"],
             "help": ("'synthetic' publishes a structure-preserving synthetic version "
                      "and keeps the raw restricted; the app generates it and a "
                      "synthesis report."),
             "maps": {"datacite": "rights (access)"}},
            {"key": "institutional_authorization",
             "label": "Institutional authorization (use and deposit)", "obligation": "M",
             "widget": "text",
             "help": "Competent Iscte body/service that authorized use and deposit. Required by the ethics opinion.",
             "maps": {"datacite": "description (Methods)", "prov": "prov:Agent"}},
            {"key": "dpo_consultation", "label": "Data Protection Officer (DPO) consultation",
             "obligation": "C", "widget": "select",
             "options": ["completed", "requested", "not started"],
             "help": "C: recommended by the ethics opinion; should be 'completed' before publishing.",
             "maps": {"datacite": "description (Methods)"}},
            {"key": "anonymization_doc",
             "label": "Anonymization / disclosure-control documentation", "obligation": "M",
             "widget": "textarea",
             "help": ("Describe the original data, the fields removed/coded/aggregated/"
                      "generalised, the data actually used, and the data published. "
                      "Goes into the Research Object as evidence."),
             "maps": {"datacite": "description (Methods)", "prov": "prov:Activity"}},
            {"key": "embargo_date", "label": "Embargo date (optional, YYYY-MM-DD)",
             "obligation": "O", "widget": "text",
             "help": "If set, publication is deferred until this date (referring to sufficiently distant academic years).",
             "maps": {"datacite": "date (Available)"}},
        ],
    },
    {
        "id": "b5",
        "title": "5 · Deposit (Zenodo)",
        "help": "Deposit fields. The DOI is pre-reserved via the API; publication is manual.",
        "fields": [
            {"key": "access_right", "label": "Access", "obligation": "M",
             "widget": "select", "options": ACCESS_RIGHTS,
             "maps": {"datacite": "rights (access)"}},
            {"key": "communities", "label": "Zenodo communities", "obligation": "O",
             "widget": "tags", "maps": {}},
        ],
    },
]


def mandatory_keys():
    """Keys of the M and C fields (conditionals are treated as M, satisfiable
    by a declaration of absence)."""
    return [f["key"] for b in FORM_BLOCKS for f in b["fields"]
            if f["obligation"] in ("M", "C")]


def all_fields():
    for block in FORM_BLOCKS:
        for field in block["fields"]:
            yield block, field
