"""Read-only research preview. Input must be produced by the forward engine."""
import json
from pathlib import Path
import streamlit as st
import pandas as pd

def render(path="forward_signals.json"):
    st.header("Qualified F&O Signals — Research Preview")
    st.warning("Paper research only. No real orders. Model score is not profit probability.")
    p=Path(path)
    if not p.exists():
        st.info("DATA UNAVAILABLE — forward signal feed is not connected.")
        return
    try:
        payload=json.loads(p.read_text())
        rows=payload["signals"]
        assert isinstance(rows,list)
    except (ValueError,KeyError,TypeError,AssertionError,OSError):
        st.error("Invalid signal feed; no actionable levels displayed.")
        return
    st.caption("Feed timestamp: "+str(payload.get("as_of","Unavailable")))
    if not rows:
        st.info("NO QUALIFIED SETUP" if payload.get("scan_complete") is True else "SCAN INCOMPLETE")
        return
    frame=pd.DataFrame(rows)
    columns=["symbol","side","strategy","signal_time_ist","model_score","spot_price","strike","expiry","lot_size","ltp","bid","ask","spread_pct","entry","stop_loss","target","risk_reward","capital_per_lot","risk_per_lot","potential_reward_per_lot","status"]
    st.dataframe(frame.reindex(columns=columns),use_container_width=True,hide_index=True)
    for row in rows:
        with st.expander(str(row.get("symbol","Unavailable"))+" | "+str(row.get("status","Unavailable"))):
            fields=["instrument_key","quote_timestamp","bid_size","ask_size","oi","oi_change","volume","delta","gamma","theta","vega","iv","vwap","adx","ema_gap","relative_strength","exit_deadline","exit_price","exit_reason","gross_pnl","estimated_charges","net_pnl","rejection_reason"]
            st.dataframe(pd.DataFrame([{"Field":k,"Value":str(row[k]) if row.get(k) is not None else "Unavailable"} for k in fields]),hide_index=True,use_container_width=True)
    st.caption("Stored signal levels are historical observations, not current executable quotes. Stop losses are not guaranteed.")

if __name__=="__main__":
    render()
