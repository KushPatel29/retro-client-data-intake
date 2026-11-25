import pandas as pd
import plotly.express as px
import streamlit as st

import core

core.header("Automation",
            "What the pipeline does that would otherwise be done by hand, how long each step actually took in this run, "
            "and an estimate of time saved using your own assumptions.")

log = core.q("SELECT * FROM run_log")
total = log.seconds.sum()
k = st.columns(3)
k[0].metric("Whole quarter processed in", f"{total:.1f} s")
k[1].metric("Records checked", f"{int(core.q('SELECT sum(row_count) n FROM file_register').n.iloc[0]):,}")
k[2].metric("Steps", len(log))
fig = px.bar(log.iloc[::-1], x="seconds", y="stage", orientation="h", text=log.iloc[::-1].apply(lambda r: f"{r.seconds:.2f}s · {r['count']:,} {r.unit}", axis=1),
             color_discrete_sequence=[core.NAVY], title="Measured time per step in this run (cloud container)")
fig.update_layout(height=380, yaxis_title=None, xaxis_title="seconds", xaxis_range=[0, log.seconds.max() * 1.45])
fig.update_traces(textposition="outside", cliponaxis=False)
st.plotly_chart(fig, width="stretch")
st.caption("Timings are measured live on each run. The first step only exists because the files are synthetic.")

st.subheader("From manual to automated")
steps = pd.DataFrame([
    ("Log each file that arrives", "Check the inbox, note it in a spreadsheet", "Hashed and registered on arrival; duplicates rejected",
     "Nothing arrives unrecorded", 5),
    ("Spot missing and late files", "Compare against a calendar by memory", "Expected-file list against the due date",
     "Missing files are chased the next day", 3),
    ("Open and reformat the file", "Excel, column by column, per client", "Mapping per client in configuration",
     "Same result every month", 20),
    ("Tie out control totals", "Sum columns, compare with the trailer", "Count and amount checked automatically",
     "Cut-off or wrong extract caught", 10),
    ("Check the records", "Filters and spot checks", "23 SQL rules on every record",
     "Every record checked, not a sample", 40),
    ("Compare with last month", "VLOOKUPs between files", "Roll-forward and face-change checks",
     "Dropped policies found", 25),
    ("Write the exception report and email", "Copy rows into an email", "Excel report and drafted follow-up",
     "Specific, consistent requests", 20),
    ("Load to production", "Manual import after sign-off", "Gate loads clean rows, holds the rest",
     "Nothing unchecked reaches production", 10),
], columns=["step", "by hand", "automated", "control gained", "default minutes per file"])
st.dataframe(steps.drop(columns=["default minutes per file"]), hide_index=True, width="stretch")

st.subheader("Time saved: your assumptions")
st.write("These are not measurements from any real team. Set the minutes per file to what you see in practice.")
c1, c2 = st.columns([2, 1])
with c1:
    mins = st.data_editor(steps[["step", "default minutes per file"]].rename(columns={"default minutes per file": "minutes by hand"}),
                          hide_index=True, width="stretch", disabled=["step"], key="mins")
with c2:
    files = st.number_input("Client files per month", 1, 2000, 12)
    review = st.number_input("Minutes still spent reviewing each file's results", 0, 120, 10)
    hours = (mins["minutes by hand"].sum() - review) * files / 60
    st.metric("Hours saved per month", f"{max(hours, 0):,.0f}")
    st.metric("Hours saved per year", f"{max(hours, 0) * 12:,.0f}")
st.caption("12 files per month is this demo's volume: four clients, three files each.")
