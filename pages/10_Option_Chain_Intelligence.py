
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:

    sys.path.insert(0, str(ROOT))

from option_chain_intelligence import (
    render_option_intelligence
)

st.set_page_config(

    page_title="Option Chain Intelligence",

    layout="wide"

)

render_option_intelligence()
