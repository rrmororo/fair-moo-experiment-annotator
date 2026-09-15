# FAIR MOO Experiment Annotator

A curation tool that turns multi-objective optimisation (MOO) experiments into
**FAIR-compliant research objects** — findable, accessible, interoperable and
reusable. It takes the files and metadata of an existing experiment, guides the
curator through validating identifiers and describing the data model, checks the
result against the FAIR principles, and packages everything as an RO-Crate ready
for deposit on Zenodo.

The tool was developed as part of a Master's dissertation on FAIR Research Data
Management for multi-objective optimisation, using **university timetabling** as
the application domain. It supports *retrospective curation*: annotating an
experiment after the fact, from the artefacts a researcher hands over.

## What it does

- **Guided curation** of a MOO experiment through a Streamlit interface: you
  describe the experiment, its algorithms, problem instances, quality indicators
  and agents, and the tool assembles the metadata for you.
- **Persistent identifier verification** — parses and confirms DOIs, ORCIDs,
  RORs and SWHIDs against the relevant registries, and resolves them through the
  EOSC PID Meta Resolver (PIDMR) where available, so every identifier in the
  final object is checked before deposit.
- **Semantic anchoring** of algorithms, problems and indicators to Wikidata
  concepts and to the MOODY ontology, with a local `fair-moo` identifier minted
  for concepts that have no external anchor.
- **FAIR checks** — a check-based report that flags incompatibilities with the
  FAIR principles (for example, a dataset published without column headers, or a
  missing licence) so they can be fixed before deposit.
- **SHACL validation** of the provenance graph as a technical gate: packaging and
  deposit only proceed once the graph conforms.
- **Disclosure control** for sensitive tabular inputs, with configurable sharing
  modes (aggregated, synthetic, or restricted raw) to support responsible sharing
  of institutional data.
- **RO-Crate packaging** of the full set of generated artefacts, ready for a
  Zenodo deposit.

## Standards and outputs

The annotator builds on established metadata standards and vocabularies:

- **RO-Crate 1.1** — the packaging format for the final research object
- **Croissant 1.0** — machine-readable description of the dataset structure
- **PROV-O** — provenance of the experiment and its artefacts
- **DataCite** — metadata for the Zenodo deposit and related identifiers
- **DCAT-AP** — dataset catalogue metadata
- **MOODY ontology** — domain typing of MOO algorithms, problems and indicators
- **SHACL** — validation of the provenance graph

Running a curation produces, among others:

| File | Purpose |
| --- | --- |
| `ro-crate-metadata.json` | RO-Crate manifest for the packaged research object |
| `croissant.json` | Croissant description of the dataset structure |
| `provenance.ttl` | PROV-O provenance graph (with MOODY / BIGOWL typing) |
| `dcat_ap.jsonld` | DCAT-AP catalogue metadata |
| `moo_vocabulary.ttl` | Local FAIR-MOO SKOS/OWL vocabulary for curated concepts |
| `zenodo_metadata.json` | Metadata for the Zenodo deposit |
| `experiment_record.json` | Consolidated, queryable record of the experiment |
| `fair_check_report.json` | FAIR compliance report |
| `shacl_report.txt` | SHACL validation report |
| `confirmation_report.md` | Human-readable summary confirmed before deposit |

## Identifier strategy

The tool assigns identifiers by entity type:

- **Objects** (the experiment, datasets, software) → **DOI**
- **People** → **ORCID** (with **ROR** for their organisation)
- **Organisations** → **ROR**
- **Software** → **SWHID** (Software Heritage)
- **Concepts** (algorithms, problems, indicators) → **Wikidata QID**, and a
  resolvable **`fair-moo`** IRI when no external identifier exists

## Installation

Requires **Python 3.12** (a `.venv312` virtual environment is assumed in the
project).

```bash
# clone the repository
git clone https://github.com/rrmororo/fair-moo-experiment-annotator.git
cd fair-moo-experiment-annotator

# create and activate a virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

# install dependencies
pip install -r requirements.txt
```

## Usage

### Curation interface

The main interface is a Streamlit app:

```bash
streamlit run app.py
```

Work through the interface in order:

1. **Describe** the experiment, algorithms, problem instances, indicators and agents.
2. **Verify PIDs** — confirm the parsed DOIs, ORCIDs, RORs and SWHIDs.
3. **Generate** the metadata artefacts.
4. **Run FAIR checks** and resolve any flagged issues.
5. **Deposit** — package the RO-Crate (unlocked once SHACL conforms) and prepare
   the Zenodo deposit.

> Run **Generate** before **Run FAIR checks** — the checks are formative and rely
> on the generated artefacts.

### Getting a Software Heritage identifier

`get_swhid.py` is a small command-line helper that returns the real SWHID for a
repository snapshot (or the "Save Code Now" URL if the repository has not yet
been archived):

```bash
python get_swhid.py <repository-url>
```

## Project structure

```
fair-moo-experiment-annotator/
├── app.py                     # Streamlit curation interface
├── schema.py                  # Data model and curator input
├── pipeline.py                # Generation of metadata artefacts
├── fair_checks.py             # FAIR compliance checks
├── pid_registry.py            # PID parsing and verification
├── pidmr.py                   # EOSC PID Meta Resolver integration
├── wikidata.py                # Wikidata lookup and item creation
├── fairmoo.py                 # Local FAIR-MOO identifier minter
├── swh.py                     # Software Heritage (SWHID) support
├── get_swhid.py               # CLI helper to obtain a repository SWHID
├── shapes/
│   └── curation_shapes.ttl    # SHACL shapes for provenance validation
├── requirements.txt
└── README.md
```

## Data protection

When curating institutional or otherwise sensitive tabular data, the tool
supports responsible-sharing modes so that raw microdata is not published openly.
Depending on the configured sharing mode it can publish **real aggregates** (with
small-cell suppression) or a **synthetic surrogate** that preserves structure,
while keeping the raw file **restricted**. Choosing a sharing mode and obtaining
any required institutional authorisation remains the curator's responsibility.

## Citation

If you use this software, please cite it. A `CITATION.cff` file and a Zenodo DOI
will be added once the research object is deposited.

## License

This project is released under the MIT License. See [`LICENSE`](LICENSE) for
details.

## Acknowledgements

Developed as part of a Master's dissertation in Integrated Decision Support
Systems (Business Intelligence) at ISCTE – University Institute of Lisbon. The
timetabling experiment data curated in the demonstration originates from prior
work at ISCTE.
