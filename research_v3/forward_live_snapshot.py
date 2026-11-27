"""Read-only intraday snapshot adapter for integration testing.
REST bootstrap is not a production streaming feed.
"""
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote
from datetime import timedelta
import math
import requests
from stock_intelligence_engine import now,parse_timestamp
from live_market_engine import load_instruments,NIFTY,SECTOR_INDICES

def snapshot(token,workers=3):
    start=now()
    minute=start.hour*60+start.minute
    universe,futures=load_instruments()
    items=[("spot",s,k) for s,k in universe.items()]
    items += [("future",s,f["key"]) for s,f in futures.items() if s in universe]
    items += [("index",s,k) for s,k in {"NIFTY":NIFTY,**SECTOR_INDICES}.items()]
    def fetch(item):
        kind,s,key=item
        try:
            r=requests.get("https://api.upstox.com/v3/historical-candle/intraday/"+quote(key,safe="")+"/minutes/1",headers={"Authorization":"Bearer "+token,"Accept":"application/json"},timeout=15)
            r.raise_for_status()
            p=r.json()
            if p.get("status")!="success":raise ValueError("API failure")
            rows={}
            for c in p["data"]["candles"]:
                t=parse_timestamp(c[0]).astimezone(start.tzinfo)
                m=t.hour*60+t.minute
                if t.date()!=start.date() or not 555<=m<minute or t.second:continue
                n=6 if kind=="future" else 5
                v=list(map(float,c[1:1+n]))
                if len(v)!=n or not all(map(math.isfinite,v)) or min(v[:4])<=0 or v[4]<0:continue
                if kind=="future" and v[5]<=0:continue
                rows[str(m)]=v
            if len(rows)!=minute-555:raise ValueError("incomplete prefix")
            return kind,s,rows,None
        except Exception as e:return kind,s,None,type(e).__name__
    data={"spot":{},"future":{},"index":{}}
    errors=[]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for kind,s,rows,error in pool.map(fetch,items):
            if error:errors.append({"kind":kind,"symbol":s,"error":error})
            else:data[kind][s]=rows
    end=now()
    return {"signal_time":start.replace(second=0,microsecond=0),"elapsed_seconds":(end-start).total_seconds(),"fresh":end<start.replace(second=0,microsecond=0)+timedelta(minutes=1),"universe":universe,"spots":data["spot"],"futures":data["future"],"indices":data["index"],"errors":errors}
