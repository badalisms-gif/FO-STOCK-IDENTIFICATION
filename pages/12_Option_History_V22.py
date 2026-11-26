
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from option_history_engine import render_history

st.set_page_config(
    page_title="Option History V2.2",
    layout="wide"
)

render_history()
