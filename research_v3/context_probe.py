import os,json,time
from pathlib import Path
from datetime import date
from stock_intelligence_engine import get_json,UP
from live_market_engine import load_instruments,NIFTY,SECTOR_INDICES
from intraday_history_engine import fetch_candles,validate_candles
def main():
    token=os.environ["UPSTOX_ACCESS_TOKEN"]
    universe,futures=load_instruments()
    day=date(2026,11,26)
    report={"universe":len(universe),"current_futures":len(futures),"date":str(day),"probes":[]}
    instruments=[(s,futures[s]["key"]) for s in ["RELIANCE","INFY","SBIN","DIXON","PAYTM"] if s in futures]
    instruments += [("NIFTY",NIFTY)]+list(SECTOR_INDICES.items())
    for symbol,key in instruments:
        try:
            raw=fetch_candles(token,key,day,day)
            valid=validate_candles(raw,[day])
            oi=[float(c[6]) for c in raw if len(c)>6 and c[6] is not None]
            report["probes"].append({"symbol":symbol,"rows":len(raw),"validated_sessions":len(valid),"oi_rows":len(oi),"positive_oi_rows":sum(x>0 for x in oi),"distinct_oi":len(set(oi))})
        except Exception as e:
            report["probes"].append({"symbol":symbol,"error_type":type(e).__name__})
        time.sleep(.5)
    p=Path("research_output");p.mkdir(exist_ok=True)
    (p/"context_probe.json").write_text(json.dumps(report,indent=2))
    print("CONTEXT_PROBE",json.dumps(report),flush=True)
if __name__=="__main__":main()
