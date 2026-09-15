# FAIR MOO Experiment Annotator — retrospective curation (single mode)

Retrospective FAIRification of an existing MOO experiment: the curator ingests
the files received from the original author (datasets, experiment and
indicator CSVs, the ITC2007 .ctt, Java code, the source dissertation PDF),
annotates the metadata manually with documentary evidence per field, declares
what the source does not record, and produces a deposit package (Croissant +
PROV-O + DataCite + experiment_record + reports) as an RO-Crate, with a draft
on Zenodo.

No optimisation code is executed — there is no prospective mode.

## Run
    pip install -r requirements.txt
    streamlit run app.py

Place the received material in a folder (e.g. `received/batista`) and point to
it in the "F · Received files" tab. For step 5, export ZENODO_TOKEN
(a sandbox.zenodo.org token; a zenodo.org token for production).

## Flow
form (C, 1–5, DP) → ingest files → 0 FAIR checks → 0.5 PIDs
(verify+confirm; QuickStatements for concepts without a QID) → 1 generate →
2 SHACL (curation profile) → 3 RO-Crate → 4 report+confirmation →
5 deposit (draft; manual publication).

## Structure
    schema.py      metadata profile (single source of truth; M/C/R/O)
    app.py         curation wizard generated from the schema
    pipeline.py    ingestion, serializers, disclosure control, SHACL, RO-Crate, Zenodo
    fair_checks.py formative FAIR verification (incl. hypervolume→ref. point conditional)
    pid_registry.py PID parsing + ORCID/ROR/Crossref APIs; QuickStatements
    pidmr.py       EOSC PID Meta Resolver integration (resolver of record)
    wikidata.py    QIDs: normalisation, URIs, search
    shapes/        curation_shapes.ttl (value+evidence OR justified absence)
