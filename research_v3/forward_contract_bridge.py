"""Read-only contract selection and paper registration for live model candidates."""
import math
from datetime import date
from zoneinfo import ZoneInfo
from stock_intelligence_engine import get_json, UP
from research_v3.forward_upstox_quotes import read_depth
from research_v3.forward_paper_execution import enter

def select_atm(token,key,spot,day,side="CE"):
    if side!="CE" or not math.isfinite(spot) or spot<=0: raise ValueError("invalid candidate")
    p=get_json(UP+"/v2/option/contract",{"instrument_key":key},token)
    if p.get("status")!="success": raise ValueError("contract API failed")
    contracts=[c for c in p["data"] if c.get("underlying_key")==key and c.get("instrument_type")==side and c.get("expiry","")>=day.isoformat()]
    expiry=min(c["expiry"] for c in contracts)
    if expiry==day.isoformat(): raise ValueError("expiry day excluded")
    return min((c for c in contracts if c["expiry"]==expiry),key=lambda c:(abs(float(c["strike_price"])-spot),float(c["strike_price"])))

def register_candidate(ledger,token,candidate):
    t=candidate["signal_time"].astimezone(ZoneInfo("Asia/Kolkata"))
    score=float(candidate["score"])
    if not math.isfinite(score) or score<.60: return "below threshold"
    if not candidate.get("technical_gate"): return "technical gate failed"
    c=select_atm(token,candidate["underlying_key"],float(candidate["spot_price"]),t.date())
    key=c["instrument_key"]; lot=int(c["lot_size"])
    sid=f'{key}:{t.isoformat()}'
    if any(r["signal_id"]==sid for r in ledger.rows(t.date().isoformat())): return "already registered"
    observed,quotes,_=read_depth(token,[key])
    q=quotes.get(key)
    position,error=enter(t,observed,q,date.fromisoformat(c["expiry"]),lot) if q else (None,"missing fresh depth")
    row={"session_date":t.date().isoformat(),"signal_id":sid,"symbol":candidate["symbol"],"side":"CE","strategy":"CE STRICT + CE A" if score>=.6513329335878008 else "CE A","first_qualified_at":t.isoformat(),"signal_time_ist":t.isoformat(),"original_score":score,"current_score":score,"spot_price":candidate["spot_price"],"instrument_key":key,"strike":float(c["strike_price"]),"expiry":c["expiry"],"lot_size":lot,"filter_status":"NEW SIGNAL","trade_status":"ACTIVE" if position else "REJECTED","rejection_reason":error}
    if q: row.update(bid=q.bid,ask=q.ask,spread_pct=100*(q.ask-q.bid)/((q.ask+q.bid)/2),quote_timestamp=q.timestamp.isoformat(),bid_size=q.bid_size,ask_size=q.ask_size)
    if position:
        e=position["entry"];sl=position["stop"];tp=position["target"]
        row.update(entry=e,stop_loss=sl,target=tp,risk_reward=(tp-e)/(e-sl),capital_per_lot=e*lot,risk_per_lot=(e-sl)*lot,potential_reward_per_lot=(tp-e)*lot,exit_deadline=position["deadline"].isoformat(),entry_time=position["time"].isoformat())
    ledger.register(row)
    return row["trade_status"]
