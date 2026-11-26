
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:

    sys.path.insert(0, str(ROOT))

from live_market_engine import render_live_market

st.set_page_config(

    page_title="Live Market Intelligence",

    layout="wide"

)

render_live_market()
