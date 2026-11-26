
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integrated_live_engine import (
    render_integrated_live
)

st.set_page_config(
    page_title="Integrated Live V2.4",
    layout="wide"
)

render_integrated_live()
