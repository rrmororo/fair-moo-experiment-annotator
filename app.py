"""
app.py — FAIR MOO Curator (retrospective curation, single mode)

The curator annotates the data and metadata of an existing MOO experiment from
the received files and the documentary sources — no optimisation code is
executed. Every extracted value carries evidence (where in the source);
what the source does not report is declared 'not recorded', with justification.

Flow: form → ingest files → 0 FAIR checks → 0.5 PIDs →
1 generate annotations → 2 SHACL → 3 RO-Crate → 4 report+confirmation →
5 Zenodo Sandbox deposit (draft; manual publication).

Run with:  streamlit run app.py
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

import schema
import pipeline
import wikidata
import fair_checks
import pid_registry

st.set_page_config(page_title="FAIR MOO Experiment Annotator", page_icon="🗂️", layout="wide")

# administrative fields: no evidence nor declaration of absence
ADMIN_KEYS = {"curators", "curation_notes", "authorization_status",
              "source_document", "access_right", "communities",
              "license", "version", "language", "title", "keywords"}

if "meta" not in st.session_state:
    st.session_state.meta = {"evidence": {}, "not_recorded": {}}
META = st.session_state.meta

# ---------------------------------------------------------------- sidebar ---
with st.sidebar:
    st.header("MOO Research Data Management")
    st.caption("A curation tool for FAIR-oriented Research Data Management "
               "of Multi-Objective Optimisation experiments. Retrospectively "
               "curate, annotate and deposit the data of an existing MOO "
               "experiment, with full provenance and evidence tracking.")
    st.divider()
    st.header("🔎 Wikidata search (QIDs)")
    st.caption("The DOI identifies the deposit; QIDs anchor concepts "
               "(algorithms, indicators, software, domain). Existing QIDs only.")
    wd_term = st.text_input("Term", key="wd_term",)
    wd_lang = st.selectbox("Language", ["en", "pt"], key="wd_lang")
    if st.button("Search on Wikidata"):
        st.session_state.wd_results, st.session_state.wd_error = \
            wikidata.search(wd_term, language=wd_lang)
    if st.session_state.get("wd_error"):
        st.warning(st.session_state.wd_error)
    for res in st.session_state.get("wd_results", []):
        st.markdown(f"**`{res['qid']}`** — {res['label']}  \n"
                    f"_{res['description'] or 'no description'}_  \n"
                    f"[open]({res['url']})")
    st.divider()
    st.header("👤 ORCID / ROR search (people and organisations)")
    st.caption("Type the name; the app queries the APIs and shows candidates "
               "for you to confirm — is this the person? is this the "
               "organisation? — before copying the ID into the table.")
    po_term = st.text_input("Person (ORCID)", key="po_term",)
    if st.button("Search person on ORCID"):
        st.session_state.orcid_results, st.session_state.orcid_error = \
            pid_registry.search_orcid(po_term)
    if st.session_state.get("orcid_error"):
        st.warning(st.session_state.orcid_error)
    elif st.session_state.get("orcid_results") == []:
        st.info("No visible matches for that name — try variants "
                "(surname only, or with/without middle names).")
    for res in st.session_state.get("orcid_results", []):
        st.markdown(f"**Is this the person?** {res['name'] or '(no public name)'}  \n"
                    f"_{res['institutions'] or 'no listed affiliation'}_")
        st.code(res["orcid"], language=None)
    ro_term = st.text_input("Organisation (ROR)", key="ro_term",)
    if st.button("Search organisation on ROR"):
        st.session_state.ror_results, st.session_state.ror_error = \
            pid_registry.search_ror(ro_term)
    if st.session_state.get("ror_error"):
        st.warning(st.session_state.ror_error)
    elif st.session_state.get("ror_results") == []:
        st.info("No matching organisations — try the official name "
                "or the acronym.")
    for res in st.session_state.get("ror_results", []):
        st.markdown(f"**Is this the organisation?** {res['name']} "
                    f"({res['country'] or '—'})")
        st.code(res["ror"], language=None)


# ------------------------------------------------------------- renderers ----
def render_field(field: dict):
    key = field["key"]
    label = field["label"] + (" *" if field["obligation"] in ("M", "C") else "")
    help_text = field.get("help")
    widget = field["widget"]

    if key not in ADMIN_KEYS:
        nr = st.checkbox("Not recorded in the source", key=f"nr_{key}",
                         value=key in META["not_recorded"])
        if nr:
            META["not_recorded"][key] = st.text_input(
                "Justification for the absence *", key=f"nrj_{key}",
                value=META["not_recorded"].get(key, ""),)
            META.pop(key, None)
            st.divider()
            return
        META["not_recorded"].pop(key, None)

    if widget == "text":
        META[key] = st.text_input(label, value=META.get(key, field.get("default", "")),
                                  help=help_text, key=f"w_{key}")
    elif widget == "textarea":
        META[key] = st.text_area(label, value=META.get(key, ""), help=help_text,
                                 key=f"w_{key}")
    elif widget == "select":
        options = field["options"]
        current = META.get(key, options[0])
        META[key] = st.selectbox(label, options,
                                 index=options.index(current) if current in options else 0,
                                 help=help_text, key=f"w_{key}")
    elif widget == "tags":
        raw = st.text_input(label, value=", ".join(META.get(key, [])),
                            help=help_text, key=f"w_{key}")
        META[key] = [t.strip() for t in raw.split(",") if t.strip()]
    elif widget == "table":
        cols = field["columns"]
        # STABLE input data: seeded once into session_state. Feeding the
        # editor back the filtered value (META[key]) made the input data
        # change on every rerun, which reset the data_editor's internal state
        # and discarded the edits (rows "disappeared").
        seed_key = f"seed_{key}"
        if seed_key not in st.session_state:
            st.session_state[seed_key] = META.get(key) or [dict.fromkeys(cols, "")]
        st.caption(label + (f" — {help_text}" if help_text else ""))
        edited = st.data_editor(st.session_state[seed_key],
                                num_rows="dynamic", key=f"w_{key}",
                                column_config={k: st.column_config.TextColumn(v)
                                               for k, v in cols.items()})
        META[key] = [r for r in edited
                     if any(str(v).strip() for v in r.values() if v is not None)]

    if key not in ADMIN_KEYS:
        ev = st.text_input("Evidence (where in the source?)", key=f"ev_{key}",
                           value=META["evidence"].get(key, ""))
        if ev.strip():
            META["evidence"][key] = ev.strip()
        else:
            META["evidence"].pop(key, None)
    st.divider()


def missing_mandatory():
    missing = []
    for block, field in schema.all_fields():
        if field["obligation"] not in ("M", "C"):
            continue
        key = field["key"]
        value = META.get(key)
        filled = value not in (None, "", [])
        declared = bool(META["not_recorded"].get(key, "").strip())
        if not (filled or declared):
            missing.append(f"{block['title']} → {field['label']}")
    return missing


# ------------------------------------------------------------------ UI ------
st.title("FAIR MOO Experiment Annotator")
st.caption("Retrospective FAIRification of MOO experiments · Croissant 1.0 + "
           "PROV-O inside RO-Crate · Zenodo deposit (draft)")

tab_labels = [b["title"] for b in schema.FORM_BLOCKS] + \
             ["F · Received files", "▶ Pipeline"]
tabs = st.tabs(tab_labels)

for tab, block in zip(tabs, schema.FORM_BLOCKS):
    with tab:
        st.info(block["help"])
        for field in block["fields"]:
            render_field(field)
            maps = field.get("maps", {})
            if maps:
                st.caption("↳ maps to: " +
                           " · ".join(f"{v} ({k})" for k, v in maps.items()))

# ---- ingestion of the received files ----
with tabs[-2]:
    st.info("Point to the folder containing the received material (datasets, "
             "experiment and indicator CSVs, the ITC2007 .ctt, Java code and the "
             "source dissertation PDF). The tool computes checksums and formats "
             "recursively; you describe what each file is.")
    files_dir = st.text_input("Folder with received files",
                              value=st.session_state.get("files_dir", ""),)
    if st.button("Ingest files"):
        try:
            # stable seed: the editor always receives the original inventory;
            # the edited descriptions are read from the returned value
            st.session_state.rf_seed = pipeline.ingest_files(Path(files_dir))
            META["received_files"] = st.session_state.rf_seed
            st.session_state.files_dir = files_dir
            st.success(f"{len(META['received_files'])} file(s) ingested.")
        except FileNotFoundError as exc:
            st.error(f"Folder not found: {exc}")
    if st.session_state.get("rf_seed"):
        st.caption("Describe each file (goes into the Croissant FileObject).")
        META["received_files"] = st.data_editor(
            st.session_state.rf_seed, key="w_received_files",
            disabled=["name", "sha256", "size_bytes", "encodingFormat", "path"],
            column_config={
                "name": st.column_config.TextColumn("File"),
                "sha256": st.column_config.TextColumn("sha256"),
                "size_bytes": st.column_config.NumberColumn("Bytes"),
                "encodingFormat": st.column_config.TextColumn("Format"),
                "description": st.column_config.TextColumn("Description (edit here)"),
            })

# ---- pipeline ----
with tabs[-1]:
    st.subheader("Verify, generate, validate, package, deposit")
    out_dir = Path("output_package")

    missing = missing_mandatory()
    if missing:
        st.warning("Mandatory fields with neither a value nor a declared absence:\n\n"
                   + "\n".join(f"- {m}" for m in missing))
    if META["not_recorded"]:
        st.info("Declared incompleteness (:unav): " + ", ".join(META["not_recorded"]))
    if not META.get("received_files"):
        st.warning("No files ingested. If the deposit is metadata-only, "
                   "this may be intentional.")

    metadata_only = str(META.get("authorization_status", "")).startswith("no authorization")
    if metadata_only:
        st.info("**Metadata-only** deposit: the received files will NOT be "
                "sent to Zenodo — only the annotation artefacts.")

    # ---- 0: FAIR verification ----
    st.markdown("#### 0 · FAIR checks (check-based)")
    if st.button("Run FAIR checks", use_container_width=True):
        st.session_state.fair_results = fair_checks.check_all(META)
        fair_checks.write_report(st.session_state.fair_results, out_dir)
    if "fair_results" in st.session_state:
        results = st.session_state.fair_results
        s = fair_checks.summary(results)
        c1, c2, c3 = st.columns(3)
        c1.metric("Errors", s["errors"]); c2.metric("Warnings", s["warnings"]); c3.metric("OK", s["ok"])
        icons = {"error": "❌", "warning": "⚠️", "info": "✅"}
        for res in results:
            icon = "✅" if res["ok"] else icons[res["severity"]]
            st.markdown(f"{icon} **[{res['principle']}] {res['id']}** — {res['message']}")

    st.divider()
    # ---- 0.5: PIDs ----
    st.markdown("#### 0.5 · PIDs: verify and confirm (ORCID · ROR · DOI)")
    st.caption("A valid format ≠ the right entity: the label returned by the API "
               "must be confirmed by you.")
    if st.button("Verify PIDs via API", use_container_width=True):
        META["pid_results"] = pid_registry.verify_all(META)
        META.setdefault("pid_confirmations", {})
    for i, p in enumerate(META.get("pid_results", [])):
        key = p.get("normalized") or p["raw"]
        label = p.get("label") or p.get("error") or "?"
        META.setdefault("pid_confirmations", {})
        META["pid_confirmations"][key] = st.checkbox(
            f"{p['kind'].upper()} `{key}` ({p['where']}) → **{label}** — I confirm",
            value=META["pid_confirmations"].get(key, False),
            key=f"pidc_{i}_{p['kind']}_{key}")

    with st.expander("Propose Wikidata items for concepts without a QID (QuickStatements)"):
        st.caption("Mainly for algorithms without an item. Submission is external "
                   "to the deposit flow and subject to Wikidata policies "
                   "(notability, sources, community review).")
        merged = {"concept_qids":
                  META.get("concept_qids", [])
                  + [{"concept": "algorithm", "label": a.get("name", ""),
                      "qid": a.get("qid", "")} for a in META.get("algorithms", [])]
                  + [{"concept": "indicator", "label": i.get("name", ""),
                      "qid": i.get("qid", "")} for i in META.get("indicators", [])]}
        qs = pid_registry.quickstatements_for_missing_concepts(merged)
        st.code(qs, language=None)
        st.download_button("Download quickstatements.txt", data=qs,
                           file_name="quickstatements.txt", mime="text/plain")

    st.divider()
    col1, col2 = st.columns(2)
    with col1:
        if st.button("1 · Generate annotations (Croissant + PROV-O + DataCite + record)",
                     disabled=bool(missing), use_container_width=True):
            artefacts = pipeline.generate_annotations(META, out_dir)
            st.session_state["shacl_conforms"] = False  # new artefacts -> revalidate
            st.session_state.artefacts = artefacts
            st.success(f"Generated: {', '.join(p.name for p in artefacts)}")
            with st.expander("Preview croissant.json"):
                st.json(json.loads((out_dir / "croissant.json").read_text(encoding="utf-8")))
            with st.expander("Preview provenance.ttl"):
                st.code((out_dir / "provenance.ttl").read_text(encoding="utf-8"), language="turtle")
        if st.button("2 · Validate SHACL (curation profile)",
                     disabled="artefacts" not in st.session_state,
                     use_container_width=True):
            conforms, report = pipeline.validate_shacl(out_dir)
            st.session_state["shacl_conforms"] = bool(conforms)
            if conforms:
                st.success("Conforms to the curation profile — packaging unlocked.")
            else:
                st.error("Not conformant — packaging stays locked until this passes:")
                st.code(report)
    with col2:
        if st.button("3 · Package RO-Crate",
                     disabled=("artefacts" not in st.session_state
                               or not st.session_state.get("shacl_conforms")),
                     help=("Enabled only after SHACL validation passes: "
                           "validation is a mandatory gate before packaging."),
                     use_container_width=True):
            crate = pipeline.pack_rocrate(META, out_dir)
            st.success(f"RO-Crate created at {crate}")
        if st.button("4 · Generate confirmation report",
                     disabled="artefacts" not in st.session_state,
                     use_container_width=True):
            st.session_state.confirmation_report = pipeline.build_confirmation_report(META)
        if st.session_state.get("confirmation_report"):
            with st.container(border=True):
                st.markdown(st.session_state.confirmation_report)
            if st.checkbox("I have reviewed and confirm ALL the information above", key="confirm_all"):
                META["confirmed_at"] = datetime.now(timezone.utc).isoformat()
                out_dir.mkdir(exist_ok=True)
                (out_dir / "confirmation_report.md").write_text(
                    st.session_state.confirmation_report, encoding="utf-8")
            else:
                META.pop("confirmed_at", None)
        sandbox = st.toggle(
            "Use Zenodo Sandbox", value=True,
            help="Off = production zenodo.org: a permanent record with a "
                 "resolvable DataCite DOI that OpenAIRE harvests.")
        if not sandbox:
            st.warning(
                "Production deposit. Once **published on the Zenodo website** the "
                "record is permanent and its DOI cannot be deleted. Before "
                "publishing, confirm the institutional dataset was de-identified and "
                "that `access_right` / `authorization_status` match the redistribution "
                "permission — a restricted or metadata-only record still exposes its "
                "metadata publicly. This app only creates the draft; you review the "
                "landing page and files on Zenodo and publish there.")
        if st.button("5 · Deposit to Zenodo (draft)",
                     disabled=(not META.get("confirmed_at")
                               or not st.session_state.get("shacl_conforms")),
                     use_container_width=True,
                     help="Only after SHACL conformance AND curator confirmation. "
                          "Creates/updates a draft; publication is a manual act "
                          "on the website."):
            try:
                doi, url = pipeline.deposit_zenodo(META, out_dir, sandbox=sandbox,
                                                   metadata_only=metadata_only)
                if sandbox:
                    tail = ("a Sandbox test identifier — not resolved in production "
                            "nor harvested by OpenAIRE.")
                else:
                    tail = ("registered in DataCite and resolvable once you publish "
                            "on the website; OpenAIRE harvests it on its next cycle.")
                st.success(
                    f"Draft created on Zenodo {'Sandbox' if sandbox else 'production'}. "
                    f"Pre-reserved DOI: {doi} — {tail}")
                st.markdown(f"[Open the deposition on Zenodo]({url})")
            except pipeline.MissingTokenError:
                target = "sandbox.zenodo.org" if sandbox else "zenodo.org"
                st.error("Set the ZENODO_TOKEN environment variable "
                         f"(a {target} token).")

    st.divider()
    st.download_button(
        "Download metadata.json (form state)",
        data=json.dumps(META, ensure_ascii=False, indent=2),
        file_name="metadata.json", mime="application/json")
