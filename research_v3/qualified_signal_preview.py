"""Read-only research preview. Input must be produced by the forward engine."""
import json
from pathlib import Path
import streamlit as st
import pandas as pd

def render(path="forward_signals.json", ledger_path="forward_ledger.sqlite3"):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import os,sqlite3
    st.header("Qualified F&O Signals — Research Preview")
    st.warning("Paper research only. No real orders. Model score is not profit probability.")
    day=st.date_input("Trading session",value=datetime.now(ZoneInfo("Asia/Kolkata")).date()).isoformat()
    try:
        if os.environ.get("SUPABASE_SERVICE_ROLE_KEY"):
            from research_v3.forward_cloud_ledger import CloudLedger
            ledger=CloudLedger()
            try:
                rows=ledger.rows(day)
                status=ledger.call("GET","fo_scan_status",{"session_date":"eq."+day})
                payload={"signals":rows,"as_of":status[0]["observed_at"] if status else None,"scan_complete":status[0]["scan_complete"] if status else False}
            finally:
                ledger.close()
        elif Path(ledger_path).exists():
            with sqlite3.connect(Path(ledger_path).resolve().as_uri()+"?mode=ro",uri=True) as db:
                rows=[json.loads(r[0]) for r in db.execute("SELECT payload FROM signals WHERE day=? ORDER BY id",(day,))]
            payload={"signals":rows,"scan_complete":False}
        elif Path(path).exists():
            payload=json.loads(Path(path).read_text())
            if payload.get("session_date")!=day: raise ValueError("wrong session")
            rows=payload["signals"]
        else:
            st.info("DATA UNAVAILABLE — forward signal feed is not connected.")
            return
        if not isinstance(rows,list):raise ValueError("invalid rows")
    except Exception:
        st.error("Signal feed unavailable; no actionable levels displayed.")
        return
    st.caption("Feed timestamp: "+str(payload.get("as_of","Unavailable")))
    if not rows:
        st.info("NO QUALIFIED SETUP" if payload.get("scan_complete") is True else "SCAN INCOMPLETE")
        return
    frame=pd.DataFrame(rows)
    columns=["symbol","side","strategy","signal_time_ist","original_score","current_score","spot_price","strike","expiry","lot_size","ltp","bid","ask","spread_pct","entry","stop_loss","target","risk_reward","capital_per_lot","risk_per_lot","potential_reward_per_lot","filter_status","trade_status","last_seen_at"]
    st.dataframe(frame.reindex(columns=columns),use_container_width=True,hide_index=True)
    for row in rows:
        with st.expander(str(row.get("symbol","Unavailable"))+" | "+str(row.get("trade_status","Unavailable"))):
            fields=["instrument_key","quote_timestamp","bid_size","ask_size","oi","oi_change","volume","delta","gamma","theta","vega","iv","vwap","adx","ema_gap","relative_strength","exit_deadline","exit_price","exit_reason","gross_pnl","estimated_charges","net_pnl","rejection_reason"]
            st.dataframe(pd.DataFrame([{"Field":k,"Value":str(row[k]) if row.get(k) is not None else "Unavailable"} for k in fields]),hide_index=True,use_container_width=True)
    st.caption("Stored signal levels are historical observations, not current executable quotes. Stop losses are not guaranteed.")

if __name__=="__main__":
    render()
