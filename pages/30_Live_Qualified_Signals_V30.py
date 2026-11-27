import streamlit as st

st.set_page_config(page_title="Live Qualified Signals V30",layout="wide")
st.title("Live Qualified Signals V30")
st.info("Forward validation preview — paper signals only. Live scanner deployment pending.")
from research_v3.qualified_signal_preview import render
render()
