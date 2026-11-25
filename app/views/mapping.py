from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

import core
from retro_intake import ai, mapping, onboarding, spec

res = core.current()
cfg = core.config()
core.header("2. Mapping and onboarding",
            "Four clients, four layouts. One configuration entry per client turns each layout into the same "
            "standard format, so adding a client is configuration, not code.")

tab1, tab2, tab3 = st.tabs(["Existing client layouts", "Onboard a new client with the AI mapping assistant",
                            "Data specifications and treaties"])

with tab1:
    cid = st.radio("Client", list(cfg["clients"]), format_func=core.cname, horizontal=True)
    c = cfg["clients"][cid]
    ftype = st.radio("File", ["inforce", "billing", "claims"], horizontal=True)
    st.caption(f"{c['country']}, {c['format']} format, dates as `{c['date_format']}`, controls in a {c['control']}, "
               f"treaty {c['treaty_id']} in {c['currency']}.")
    name = c["filename"].format(file_type=ftype, yyyymm="202607")
    path = Path(res["inbox"]) / name
    if path.exists():
        parsed = mapping.read_raw(path, c, ftype)
        df, _ = mapping.standardize(parsed, c, ftype, {"file_id": "X", "cedant_id": cid, "period": "2026-07"})
        a, b = st.columns(2)
        with a:
            st.markdown(f"**As the client sends it** (`{name}`)")
            if c["format"] == "fixed":
                st.code("\n".join(path.read_text().splitlines()[:6]), language=None)
            else:
                st.dataframe(pd.DataFrame(parsed.rows[:6]), hide_index=True, width="stretch")
        with b:
            st.markdown("**After mapping to the standard format**")
            keep = [col for col in df.columns if not col.endswith("_raw") and col not in
                    ("file_id", "row_num", "cedant_id", "period", "period_start", "period_end", "record_key")]
            st.dataframe(df[keep].head(6), hide_index=True, width="stretch")
    st.markdown("**The mapping**")
    rows = []
    for std, src in c["columns"][ftype].items():
        rows.append({"standard field": std, "client column": src,
                     "codes": ", ".join(f"{k}→{v}" for k, v in c["codes"].get(std, {}).items()),
                     "unit multiplier": str(c.get("multipliers", {}).get(std, ""))})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

with tab2:
    st.write("A new client, **Aurora Life Re** (fictional), sends its first inforce file in a layout we have never seen. "
             "The assistant profiles every column (name, value patterns, date formats, code sets, magnitudes), suggests "
             "the mapping with a confidence score, and then test-runs it through the real inforce rules before "
             "anyone signs it off.")
    core.ai_mode_badge()
    src = st.radio("Sample", ["Aurora Life Re sample file", "Upload your own CSV"], horizontal=True)
    if src == "Upload your own CSV":
        up = st.file_uploader("CSV with a header row", type=["csv"])
        if up is None:
            st.stop()
        headers, rows = onboarding.read_csv_text(up.getvalue().decode("utf-8", "replace"))
    else:
        headers, rows, text = onboarding.sample_file()
        st.download_button("Download the sample", text, file_name="AURORA_inforce_202609.csv")
    st.dataframe(pd.DataFrame(rows[:5]), hide_index=True, width="stretch")

    sug = ai.suggest_mapping(headers, rows)
    st.markdown("**Suggested mapping.** Review and change any row; nothing is used until you test it.")
    grid = pd.DataFrame([{"standard field": f, "client column": v["source"], "confidence": v["confidence"],
                          "evidence": v["values"], "sample values": ", ".join(map(str, v["sample"][:4]))}
                         for f, v in sug["fields"].items()])
    edited = st.data_editor(grid, hide_index=True, width="stretch", disabled=["standard field", "confidence",
                            "evidence", "sample values"], key="mapgrid",
                            column_config={"client column": st.column_config.SelectboxColumn(options=[None] + headers),
                                           "confidence": st.column_config.ProgressColumn(min_value=0, max_value=1, format="%.2f")})
    for _, r in edited.iterrows():
        sug["fields"][r["standard field"]]["source"] = r["client column"]
    a, b, d = st.columns(3)
    sug["date_format"] = a.text_input("Date format", sug["date_format"])
    sug["multipliers"]["face_amount"] = b.number_input("Face amount multiplier", value=float(sug["multipliers"]["face_amount"]))
    sug["multipliers"]["retro_share"] = d.number_input("Retro share multiplier", value=float(sug["multipliers"]["retro_share"]), format="%.3f")
    st.markdown("**Suggested code maps** (unrecognised values stay unmapped and will be flagged)")
    st.json(sug["codes"], expanded=False)
    t1, t2, t3 = st.columns(3)
    base = dict(onboarding.NEW_CLIENT)
    base["currency"] = t1.text_input("Treaty currency", base["currency"])
    base["max_issue_age"] = t2.number_input("Max issue age", value=base["max_issue_age"])
    base["jumbo_limit"] = t3.number_input("Jumbo limit", value=base["jumbo_limit"], step=1_000_000)

    if ai.llm_available() and st.button("Ask Claude to review the mapping"):
        with st.spinner("Asking Claude..."):
            try:
                review = ai.complete("You review column mappings for life reinsurance seriatim files. Point out risky or "
                                     "wrong mappings and missing fields in at most 6 bullets.",
                                     f"Headers: {headers}\nSample rows: {rows[:5]}\nProposed: "
                                     f"{ {f: v['source'] for f, v in sug['fields'].items()} }\nCodes: {sug['codes']}")
                st.info(review)
            except Exception as e:
                st.warning(f"Claude was not reachable ({e}); the offline suggestion stands.")

    if st.button("Test this mapping on the sample", type="primary"):
        client_cfg = ai.mapping_to_config(sug, base)
        out = onboarding.test_mapping(client_cfg, headers, rows)
        st.session_state["maptest"] = (out, client_cfg)
        changed = sum(1 for _, r in edited.iterrows() if r["client column"] != grid.set_index("standard field").loc[r["standard field"], "client column"])
        core.log_ai("Mapping assistant", "offline profiler", f"{len(headers)} columns, {len(rows)} rows",
                    f"{sum(1 for v in sug['fields'].values() if v['source'])} fields mapped",
                    f"Reviewed by user ({changed} changed) and test-run in memory")
    if "maptest" in st.session_state:
        out, client_cfg = st.session_state["maptest"]
        bad = out["exceptions"]
        crit = bad[bad.severity == "CRITICAL"].row_num.nunique() if len(bad) else 0
        k = st.columns(4)
        k[0].metric("Rows mapped", out["rows"])
        k[1].metric("Fields with no column", len(out["missing_fields"]))
        k[2].metric("Rows failing critical rules", crit)
        k[3].metric("Ready for sign-off", "Yes" if not out["missing_fields"] and crit == 0 else
                    ("After the client answers" if not out["missing_fields"] else "No: fields unmapped"))
        st.dataframe(out["preview"], hide_index=True, width="stretch")
        if len(bad):
            st.markdown("**What the rules found in the sample**: ask the client about these before go-live.")
            st.dataframe(bad, hide_index=True, width="stretch")
        st.markdown("**Configuration entry to add to `config/clients.yaml` after sign-off**")
        entry = {"C05": {k: v for k, v in client_cfg.items() if k in ("name", "treaty_id", "currency", "min_issue_age",
                 "max_issue_age", "jumbo_limit", "format", "delimiter", "date_format", "columns", "codes", "multipliers")}}
        st.code(yaml.safe_dump(entry, sort_keys=False, allow_unicode=True), language="yaml")
    core.guardrails(["The assistant only suggests; a person reviews every field and code before go-live.",
                     "The test run happens in memory; nothing reaches the warehouse.",
                     "Unrecognised codes are never guessed; they stay unmapped and get flagged."])

with tab3:
    st.write("The data specification is the agreement with each client: file names, formats, controls, deadlines and "
             "every field with its codes and units. It is generated from the same configuration the pipeline runs on, so "
             "the document and the code cannot drift apart.")
    st.markdown("**Treaty register**")
    st.dataframe(pd.DataFrame([{"client": c["name"], "country": c["country"], "treaty": c["treaty_id"],
                                "currency": c["currency"], "issue ages": f"{c['min_issue_age']}-{c['max_issue_age']}",
                                "jumbo limit": f"{c['jumbo_limit']:,}", "default retro share": f"{c['retro_share_default']:.0%}",
                                "file format": c["format"], "controls": c["control"]} for c in cfg["clients"].values()]),
                 hide_index=True, width="stretch")
    sid = st.selectbox("Specification for", list(cfg["clients"]), format_func=core.cname, key="specpick")
    doc = spec.build(cfg["clients"][sid], cfg["due_day"])
    st.download_button("Download the specification (.md)", doc, file_name=f"data_specification_{sid}.md")
    with st.container(border=True):
        st.markdown(doc)
