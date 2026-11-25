import hashlib
import io
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

import core
from retro_intake import intake, mapping

res = core.current()
core.header("1. Intake and file tracking",
            "Every file is registered before anyone opens it: client, type, month, size, arrival time and a SHA-256 "
            "hash. Duplicates are rejected by hash even when renamed, late files are flagged against the 10th of the "
            "next month, and expected files that never came are listed.")

reg = core.q("""SELECT file_id, file_name, cedant_id, file_type, period, received_at, due_date, days_late,
                       bytes, row_count, status, duplicate_of, sha256 FROM file_register ORDER BY received_at""")
miss = core.q("SELECT * FROM missing_file")
core.kpis([(len(reg), "files registered"), (int((reg.days_late > 0).sum()), "received late", True),
           (len(miss), "expected but missing", True), (int((reg.status == "REJECTED_DUPLICATE").sum()), "duplicates rejected"),
           (f"{reg.bytes.sum() / 1e6:.1f} MB", "received this quarter")])

st.subheader("When files arrived, against their due date")
plot = reg.copy()
plot["client"] = plot.cedant_id.map(core.cname)
plot["arrival"] = plot.apply(lambda r: "Late" if r.days_late > 0 else ("Duplicate" if r.status == "REJECTED_DUPLICATE" else "On time"), axis=1)
fig = px.strip(plot, x="received_at", y="client", color="arrival", hover_data=["file_name", "days_late"],
               color_discrete_map={"On time": core.NAVY, "Late": core.LATE, "Duplicate": core.SLATE})
for d in sorted(plot.due_date.dropna().unique()):
    fig.add_vline(x=pd.Timestamp(d), line_dash="dot", line_color=core.MAG)
    fig.add_annotation(x=pd.Timestamp(d), y=1.06, yref="paper", text=f"due {pd.Timestamp(d):%b %d}", showarrow=False,
                       font=dict(color=core.MAG, size=11))
fig.update_traces(marker=dict(size=11))
fig.update_layout(height=320, yaxis_title=None, xaxis_title=None, legend_title=None)
st.plotly_chart(fig, width="stretch")

c1, c2 = st.columns([3, 2])
with c1:
    st.subheader("File register")
    f1, f2 = st.columns(2)
    who = f1.multiselect("Client", sorted(reg.cedant_id.unique()), format_func=core.cname)
    stat = f2.multiselect("Status", sorted(reg.status.unique()))
    view = core.filt(reg, cedant_id=who, status=stat)
    st.dataframe(view.drop(columns=["sha256"]), hide_index=True, width="stretch", height=360,
                 column_config={"bytes": st.column_config.NumberColumn("bytes", format="%d"),
                                "days_late": st.column_config.NumberColumn("days late")})
with c2:
    st.subheader("Missing files")
    if len(miss):
        st.dataframe(miss, hide_index=True, width="stretch")
    else:
        st.success("Every expected file arrived.")
    st.subheader("Duplicates caught by hash")
    dup = reg[reg.status == "REJECTED_DUPLICATE"]
    for _, d in dup.iterrows():
        orig = reg[reg.file_id == d.duplicate_of].iloc[0]
        st.markdown(f"`{d.file_name}` has the same SHA-256 as `{orig.file_name}`:  \n`{d.sha256[:32]}...`")
    if dup.empty:
        st.write("None this quarter.")

st.subheader("Try it: drop in a client file")
st.write("Upload a file named like a client's files (for example a copy of `NBL_billing_202608.csv`, edited or not). "
         "It is hashed, matched to a client, checked for duplicates, parsed in that client's layout and checked "
         "against its control totals, exactly as on intake. Nothing is stored.")
samples = sorted(Path(res["inbox"]).glob("*"))
pick = st.selectbox("Or download a sample to edit", [p.name for p in samples if p.suffix != ".ctl"], index=0)
st.download_button("Download sample file", data=(Path(res["inbox"]) / pick).read_bytes(), file_name=pick)
up = st.file_uploader("Client file", type=["csv", "txt", "xlsx", "dat"])
if up is not None:
    data = up.getvalue()
    h = hashlib.sha256(data).hexdigest()
    cfg = core.config()
    found = next(((cid, rx.match(up.name)) for cid, rx in intake.filename_patterns(cfg) if rx.match(up.name)), None)
    same = reg[reg.sha256 == h]
    st.markdown(f"**SHA-256** `{h}`")
    if len(same):
        st.error(f"Duplicate: identical to {same.iloc[0].file_name}, received {same.iloc[0].received_at:%b %d}. It would be rejected.")
    if not found:
        st.error("The file name does not match any client's agreed pattern. It would go to the unrecognised folder.")
    else:
        cid, m = found
        ftype, yyyymm = m.group("file_type").lower(), m.group("yyyymm")
        period = f"{yyyymm[:4]}-{yyyymm[4:]}"
        c = cfg["clients"][cid]
        st.success(f"Recognised: {c['name']}, {ftype} file for {period}.")
        tmp = Path(res["work"]) / "upload"
        tmp.mkdir(exist_ok=True)
        path = tmp / up.name
        path.write_bytes(data)
        ctl = Path(res["inbox"]) / Path(up.name).with_suffix(".ctl").name
        if c["control"] == "sidecar" and ctl.exists():
            (tmp / ctl.name).write_bytes(ctl.read_bytes())
        try:
            parsed = mapping.read_raw(path, c, ftype)
            df, missing = mapping.standardize(parsed, c, ftype, {"file_id": "UPLOAD", "cedant_id": cid, "period": period})
            chk = mapping.control_check(parsed, df, ftype)
            a, b, d = st.columns(3)
            a.metric("Records", f"{chk['row_count']:,}", delta=None if chk["control_count"] is None else
                     f"{chk['row_count'] - chk['control_count']:+,} vs control", delta_color="inverse")
            b.metric("Amount total", f"{chk['amount_total']:,.2f}", delta=None if chk["control_total"] is None else
                     f"{chk['amount_total'] - chk['control_total']:+,.2f} vs control", delta_color="inverse")
            d.metric("Layout", "As agreed" if not parsed.schema_issues else "Changed")
            for kind, col in parsed.schema_issues:
                st.warning(f"{kind.replace('_', ' ').title()}: {col}")
            ok = chk["control_count"] == chk["row_count"] and chk["control_total"] is not None and \
                abs(chk["control_total"] - chk["amount_total"]) <= cfg["amount_tolerance"] and not missing
            (st.success if ok else st.error)("File-level checks pass; it would go on to the 23 rules." if ok else
                                             "A file-level check fails; this file would be held.")
            st.dataframe(df.head(20), hide_index=True, width="stretch")
        except Exception as e:  # a hand-edited file can be malformed in many ways
            st.error(f"The file could not be read in {c['name']}'s layout: {e}")
