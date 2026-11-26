
import streamlit as st

from final_stock_identification_engine import (
    render_final_dashboard
)

st.set_page_config(
    page_title="F&O Stock Identification V2.8",
    layout="wide"
)

render_final_dashboard()
