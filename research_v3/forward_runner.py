"""Orchestrate a single synchronized scanner tick and paper monitoring tick.
Caller supplies completed candle snapshots from a live feed. No order execution.
"""
from datetime import datetime
from zoneinfo import ZoneInfo
from research_v3.forward_live_scoring import score_prefix
from research_v3.forward_contract_bridge import register_candidate
from research_v3.forward_monitor import monitor
from research_v3.forward_tracking import merge_scan

def tick(ledger,token,bundle,universe,spots,futures,indices,signal_time):
    t=signal_time.astimezone(ZoneInfo("Asia/Kolkata"))
    day=t.date().isoformat()
    minute=t.hour*60+t.minute
    monitor(ledger,token,day)
    if t.weekday()>=5 or not 586<=minute<=870:return {"status":"outside entry window"}
    if len(spots)<.95*len(universe):return {"status":"incomplete universe"}
    scores=score_prefix(bundle,spots,futures,indices,minute)
    lookup={r["symbol"]:r for r in scores}
    observations={}
    for row in ledger.rows(day):
        s=lookup.get(row["symbol"])
        if s:
            threshold=.6513329335878008 if "STRICT" in row.get("strategy","") else .60
            observations[row["signal_id"]]={"fresh":True,"current_score":float(s["score"]),"qualifies":bool(s["technical_gate"] and s["score"]>=threshold)}
    merge_scan(ledger,day,[],observations,scan_complete=True)
    results=[]
    for s in sorted(scores,key=lambda x:x["score"],reverse=True):
        if not s["technical_gate"] or s["score"]<.60:continue
        prior=[r for r in ledger.rows(day) if r["symbol"]==s["symbol"]]
        if any(r.get("trade_status")=="ACTIVE" or (t-datetime.fromisoformat(r["first_qualified_at"])).total_seconds()<900 for r in prior):continue
        try:
            status=register_candidate(ledger,token,{**s,"signal_time":t,"underlying_key":universe[s["symbol"]]})
        except Exception as e:
            status="registration error: "+type(e).__name__
        results.append({"symbol":s["symbol"],"status":status})
    ledger.export(day,scan_complete=True)
    return {"status":"scan complete","scored":len(scores),"registrations":results}
