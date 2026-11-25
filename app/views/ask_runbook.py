import streamlit as st

import core
from retro_intake import ai
from retro_intake.common import ROOT

core.header("Ask the runbook",
            "Retrieval-augmented answers from the team's runbook and the rule catalogue. The assistant finds the most "
            "relevant passages (BM25 search), answers only from them, and cites each one, so you can check it.")
core.ai_mode_badge()
rb = core.runbook()
use_llm = ai.llm_available() and st.toggle("Let Claude write the answer from the retrieved passages", value=True)

QUESTIONS = ["When is a file held?", "What do we do when a control total does not match?",
             "How do we onboard a new client?", "A client keeps sending duplicates. What should we do?",
             "Can AI decide what loads to production?", "How long do we keep client files?",
             "How do we test a new rule before it goes live?", "When do we escalate?"]
cols = st.columns(4)
for i, qq in enumerate(QUESTIONS):
    if cols[i % 4].button(qq, key=f"rq{i}", width="stretch"):
        st.session_state["rb_q"] = qq
typed = st.chat_input("Ask about the intake process...")
if typed:
    st.session_state["rb_q"] = typed
q = st.session_state.get("rb_q", QUESTIONS[0])

hits = rb.search(q, k=3)
with st.chat_message("user"):
    st.write(q)
with st.chat_message("assistant", avatar=":material/menu_book:"):
    if not hits:
        st.write("The runbook does not cover this. Ask the Client Administration manager.")
    else:
        answer = None
        if use_llm:
            try:
                with st.spinner("Claude is answering from the passages..."):
                    answer = rb.answer_llm(q, hits)
            except Exception as e:
                st.warning(f"Claude was not reachable ({e}); showing the extractive answer.")
        st.write(answer or rb.answer_offline(q, hits))
        st.caption("Sources")
        for n, h in enumerate(hits, start=1):
            with st.expander(f"[{n}] {h['source']}: {h['title']}  (relevance {h['score']})"):
                st.write(h["text"])

with st.expander("Read the whole runbook"):
    st.markdown((ROOT / "docs" / "runbook.md").read_text(encoding="utf-8"))
core.guardrails(["Answers come only from the retrieved passages, each cited; if nothing relevant is found, it says so.",
                 "Offline mode picks the matching sentences from the top passages, word for word.",
                 "The runbook is a sample written for this project, not BHG Re's procedures."])
