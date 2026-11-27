"""Merge scanner observations into persistent paper ledger; never delete on refresh."""
from research_v3.forward_signal_ledger import Ledger

def merge_scan(ledger,day,qualified,observations,scan_complete=False):
    for row in qualified:
        if row["session_date"]!=day: raise ValueError("wrong session")
        ledger.register(row)
    for row in ledger.rows(day):
        sid=row["signal_id"]
        obs=observations.get(sid)
        if obs is None or not obs.get("fresh"):
            ledger.update(day,sid,{"filter_status":"DATA STALE"})
            continue
        changes={k:obs[k] for k in ("current_score","current_premium","bid","ask","spread_pct","oi","volume","last_seen_at") if k in obs}
        if obs.get("qualifies") is True:
            changes["filter_status"]="REQUALIFIED" if row.get("filter_status") in ("FILTER LOST","DATA STALE") else "QUALIFIED"
            if "last_seen_at" in obs: changes["last_qualified_at"]=obs["last_seen_at"]
        elif obs.get("qualifies") is False:
            changes["filter_status"]="FILTER LOST"
        else:
            changes["filter_status"]="DATA STALE"
        ledger.update(day,sid,changes)
    ledger.export(day,scan_complete=scan_complete)
