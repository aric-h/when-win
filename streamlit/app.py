"""When-Win — navigation entry point.

Thin wrapper that sets site-wide config, renders the shared
When-Win branding header, and delegates to page scripts via
st.navigation.
"""

import streamlit as st

st.set_page_config(page_title="When-Win", layout="wide")
st.markdown("#### When-Win")

pg = st.navigation(
    [
        st.Page("win_occurrences.py", title="3+ Win Occurrences"),
        st.Page("pages/1_Fan_Happiness_Index.py", title="Fan Happiness Index"),
    ]
)
pg.run()
