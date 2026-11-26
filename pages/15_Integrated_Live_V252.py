
import streamlit as st

st.set_page_config(
    page_title="Integrated Live V2.5.2",
    layout="wide"
)

from integrated_live_v252 import (
    render_integrated_live
)

render_integrated_live()
