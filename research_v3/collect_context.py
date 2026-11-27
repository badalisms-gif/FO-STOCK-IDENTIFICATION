import os,json,gzip,hashlib,time,math
from pathlib import Path
from datetime import date,timedelta
from live_market_engine import load_instruments,NIFTY,SECTOR_INDICES
from intraday_history_engine import fetch_candles,validate_candles
from stock_intelligence_engine import parse_timestamp,now
def main():
    root=Path("research_output")
    m=json.loads((root/"manifest.json").read_text())
    days=sorted({date.fromisoformat(d) for meta in m["stocks"].values() for d in meta["dates"]})
    token=os.environ["UPSTOX_ACCESS_TOKEN"]
    universe,futures=load_instruments()
    instruments=[("index",s,k,None) for s,k in {"NIFTY":NIFTY,**SECTOR_INDICES}.items()]
    instruments += [("future",s,f["key"],f["expiry"]) for s,f in sorted(futures.items()) if s in m["stocks"]]
    out=root/"context";out.mkdir(exist_ok=True)
    report={"requested_dates":[str(d) for d in days],"contract_policy":"Current contract only; not historical front month; no rollover stitching","coverage":[],"errors":[]}
    for kind,s,key,expiry in instruments:
        sessions={}
        first=days[0]
        while first<=days[-1]:
            last=min(first+timedelta(days=26),days[-1])
            wanted=[d for d in days if first<=d<=last]
            if wanted:
                try:
                    raw=fetch_candles(token,key,first,last)
                    valid=validate_candles(raw,wanted)
                    oi={}
                    if kind=="future":
                        for c in raw:
                            try:
                                t=parse_timestamp(c[0]).astimezone(now().tzinfo)
                                v=float(c[6])
                                if math.isfinite(v) and v>0:
                                    oi.setdefault(t.date(),{})[t.hour*60+t.minute]=v
                            except (ValueError,TypeError,IndexError,AttributeError):pass
                    for d,rows in valid.items():
                        if kind=="future" and set(oi.get(d,{}))!=set(rows):continue
                        sessions[str(d)]={str(k):[ *v,oi[d][k]] if kind=="future" else list(v) for k,v in rows.items()}
                except Exception as e:
                    report["errors"].append({"symbol":s,"kind":kind,"first":str(first),"error":type(e).__name__})
                time.sleep(.5)
            first=last+timedelta(days=1)
        name=hashlib.sha256((kind+key).encode()).hexdigest()+".json.gz"
        blob=gzip.compress(json.dumps({"symbol":s,"kind":kind,"key":key,"expiry":expiry,"sessions":sessions},separators=(",",":"),allow_nan=False).encode())
        (out/name).write_bytes(blob)
        report["coverage"].append({"symbol":s,"kind":kind,"expiry":expiry,"sessions":len(sessions),"dates":sorted(sessions),"file":name,"sha256":hashlib.sha256(blob).hexdigest()})
        print(kind,s,len(sessions),flush=True)
    (out/"manifest.json").write_text(json.dumps(report,indent=2))
    print("CONTEXT_SUMMARY",json.dumps({"instruments":len(instruments),"errors":len(report["errors"]),"future_sessions":sum(x["sessions"] for x in report["coverage"] if x["kind"]=="future"),"index_sessions":sum(x["sessions"] for x in report["coverage"] if x["kind"]=="index")}),flush=True)
if __name__=="__main__":main()
