"""Paper monitoring loop step; no order endpoints."""
from datetime import datetime
from research_v3.forward_upstox_quotes import read_depth
from research_v3.forward_paper_execution import exit_check

def monitor(ledger,token,day):
    active=[r for r in ledger.rows(day) if r.get("trade_status")=="ACTIVE"]
    if not active:return
    now,quotes,_=read_depth(token,list({r["instrument_key"] for r in active}))
    for r in active:
        q=quotes.get(r["instrument_key"])
        deadline=datetime.fromisoformat(r["exit_deadline"])
        if q is None:
            changes={"filter_status":"DATA STALE"}
            if now>=deadline:changes.update(trade_status="UNPRICED EXIT",exit_reason="missing quote at deadline")
            ledger.update(day,r["signal_id"],changes)
            continue
        pos={"entry":r["entry"],"stop":r["stop_loss"],"target":r["target"],"lot_size":r["lot_size"],"time":datetime.fromisoformat(r["entry_time"]),"deadline":deadline}
        result,error=exit_check(pos,now,q)
        changes={"bid":q.bid,"ask":q.ask,"spread_pct":100*(q.ask-q.bid)/((q.ask+q.bid)/2),"last_seen_at":q.timestamp.isoformat(),"paper_pnl":(q.bid-r["entry"])*r["lot_size"]}
        if result:
            changes.update(trade_status={"stop":"SL HIT","target":"TARGET HIT","timeout":"TIME EXIT"}[result["reason"]],exit_price=result["exit"],exit_reason=result["reason"],gross_pnl=result["gross_pnl"])
        elif error=="unpriced_exit":changes.update(trade_status="UNPRICED EXIT",exit_reason=error)
        ledger.update(day,r["signal_id"],changes)
    ledger.export(day)
