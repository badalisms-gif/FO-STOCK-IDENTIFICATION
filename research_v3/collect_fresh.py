import os,json,gzip,hashlib,time,requests
from urllib.parse import quote
from collections import Counter
from pathlib import Path
from datetime import date
from live_market_engine import load_instruments,NIFTY,SECTOR_INDICES
from intraday_history_engine import fetch_candles,validate_candles
from stock_intelligence_engine import parse_timestamp,now

def main():
    day=date.fromisoformat(os.environ.get("VALIDATION_DATE","2026-11-27"))
    assert day>date(2026,11,26)
    token=os.environ["UPSTOX_ACCESS_TOKEN"]
    universe,futures=load_instruments()
    out=Path("fresh_data");out.mkdir(exist_ok=True)
    report={"date":str(day),"coverage":[],"errors":[]}
    items=[("spot",s,k,None) for s,k in sorted(universe.items())]+[("future",s,f["key"],f["expiry"]) for s,f in sorted(futures.items()) if s in universe]+[("index",s,k,None) for s,k in {"NIFTY":NIFTY,**SECTOR_INDICES}.items()]
    for kind,s,key,expiry in items:
        try:
            if kind=="future" and expiry<str(day):raise ValueError("expired current contract")
            try:
                raw=fetch_candles(token,key,day,day)
            except Exception:
                raw=[]
            if not validate_candles(raw,[day]).get(day) and day==now().date():
                response=requests.get("https://api.upstox.com/v3/historical-candle/intraday/"+quote(key,safe="")+"/minutes/1",headers={"Authorization":"Bearer "+token,"Accept":"application/json"},timeout=40)
                payload=response.json()
                if not response.ok or payload.get("status")!="success":
                    raise ValueError("intraday HTTP "+str(response.status_code)+" codes "+str([e.get("errorCode") for e in payload.get("errors",[]) if isinstance(e,dict)]))
                raw=payload["data"]["candles"]
            if not raw:raise ValueError("empty historical candles")
            valid=validate_candles(raw,[day]).get(day,{})
            oi={}
            if kind=="future":
                for c in raw:
                    t=parse_timestamp(c[0]).astimezone(now().tzinfo)
                    if t.date()==day and len(c)>6 and float(c[6])>0:oi[t.hour*60+t.minute]=float(c[6])
                valid={m:[*v,oi[m]] for m,v in valid.items() if m in oi}
            name=hashlib.sha256((kind+key).encode()).hexdigest()+".json.gz"
            blob=gzip.compress(json.dumps({"symbol":s,"kind":kind,"key":key,"expiry":expiry,"rows":valid},allow_nan=False).encode())
            (out/name).write_bytes(blob)
            report["coverage"].append({"kind":kind,"symbol":s,"rows":len(valid),"file":name,"sha256":hashlib.sha256(blob).hexdigest()})
        except Exception as e:report["errors"].append({"kind":kind,"symbol":s,"error":str(e) if isinstance(e,ValueError) else type(e).__name__})
        time.sleep(.5)
    (out/"manifest.json").write_text(json.dumps(report,indent=2))
    print("FRESH_COVERAGE",json.dumps({k:sum(x["kind"]==k and x["rows"]==375 for x in report["coverage"]) for k in ("spot","future","index")}),flush=True)
    print("FRESH_ERRORS",json.dumps(dict(Counter(x["error"] for x in report["errors"]))),flush=True)
    print("FRESH_ROW_COUNTS",json.dumps(dict(Counter(str(x["rows"]) for x in report["coverage"]))),flush=True)
    assert any(x["kind"]=="spot" and x["rows"]==375 for x in report["coverage"])
if __name__=="__main__":main()
