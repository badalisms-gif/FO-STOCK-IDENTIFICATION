
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from opportunity_engine import (
    render_opportunities
)

st.set_page_config(
    page_title="Stock Opportunity",
    layout="wide"
)

render_opportunities()
