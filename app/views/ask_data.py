import plotly.express as px
import streamlit as st

import core
from retro_intake import ai, safesql

core.header("Ask the data",
            "Ask a question in plain English. The assistant writes the SQL, shows it to you, and runs it on a "
            "read-only copy of the warehouse. You can edit the SQL and run it again.")
core.ai_mode_badge()
use_llm = ai.llm_available() and st.toggle("Use Claude to write the SQL", value=True)

st.markdown("**Try one:**")
cols = st.columns(4)
for i, ex in enumerate(ai.EXAMPLES):
    if cols[i % 4].button(ex, key=f"ex{i}", width="stretch"):
        st.session_state["ask"] = ex
q = st.chat_input("Ask about files, exceptions, clients, months, premium, scores...")
if q:
    st.session_state["ask"] = q
history = st.session_state.setdefault("ask_history", [])

question = st.session_state.pop("ask", None)
if question:
    con = core.safe_con()
    try:
        how = "offline intent parser"
        if use_llm:
            try:
                sql, expl = ai.ask_data_llm(question, safesql.schema(con))
                how = "Claude"
            except Exception as e:
                st.warning(f"Claude was not reachable ({e}); using the offline parser.")
                sql, expl = ai.ask_data_offline(question)
        else:
            sql, expl = ai.ask_data_offline(question)
        try:
            df = safesql.run(con, sql)
            err = None
        except safesql.Refused as e:
            df, err = None, f"Refused by guardrails: {e}"
        except Exception as e:
            df, err = None, f"Query failed: {e}"
        history.insert(0, {"q": question, "sql": sql, "expl": expl, "how": how, "df": df, "err": err})
        core.log_ai("Ask the data", how, question, sql.replace("\n", " "),
                    err or f"SQL shown; ran read-only, {len(df)} rows")
    finally:
        con.close()

for n, h in enumerate(history[:6]):
    with st.chat_message("user"):
        st.write(h["q"])
    with st.chat_message("assistant", avatar=":material/database:"):
        st.write(f"{h['expl']} *(SQL written by the {h['how']})*")
        with st.expander("SQL", expanded=n == 0):
            st.code(h["sql"], language="sql")
        if h["err"]:
            st.error(h["err"])
        elif h["df"] is not None:
            df = h["df"]
            st.dataframe(df, hide_index=True, width="stretch")
            num = [c for c in df.columns if df[c].dtype.kind in "if"]
            cat = [c for c in df.columns if df[c].dtype == object]
            if n == 0 and 1 < len(df) <= 40 and num and cat:
                fig = px.bar(df, x=num[0], y=cat[0] if len(cat) == 1 else df[cat[:2]].astype(str).agg(" ".join, axis=1),
                             orientation="h", color_discrete_sequence=[core.NAVY])
                fig.update_layout(height=min(60 + 28 * len(df), 520), yaxis_title=None, yaxis=dict(autorange="reversed"))
                st.plotly_chart(fig, width="stretch", key=f"askfig{len(history)}")

if history:
    st.subheader("Edit the last query")
    sql = st.text_area("SQL", history[0]["sql"], height=160)
    if st.button("Run edited SQL"):
        con = core.safe_con()
        try:
            st.dataframe(safesql.run(con, sql), hide_index=True, width="stretch")
        except Exception as e:
            st.error(str(e))
        finally:
            con.close()

core.guardrails(["The SQL is always shown before you rely on the answer.",
                 "Read-only connection; only one SELECT; no file, network or settings access; 500-row limit.",
                 "With Claude, only the table and column names are sent, never the client records.",
                 "Offline mode uses a transparent keyword parser: client, month, file, severity and topic words."])
