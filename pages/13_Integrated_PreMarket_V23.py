
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from premarket_quality_engine import (
    render_premarket
)

st.set_page_config(
    page_title="Integrated Pre-Market V2.3.1",
    layout="wide"
)

render_premarket()
