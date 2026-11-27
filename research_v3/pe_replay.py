import os,json,time,gzip,math
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from urllib.parse import quote
import requests
from live_market_engine import load_instruments
def main():
    root=Path("research_output");out=Path("pe_replay");out.mkdir(exist_ok=True)
    m=json.loads((root/"manifest.json").read_text())
    session=requests.Session();session.headers.update({"Authorization":"Bearer "+os.environ["UPSTOX_ACCESS_TOKEN"],"Accept":"application/json"})
    def get(path,params=None):
        r=session.get("https://api.upstox.com"+path,params=params,timeout=40);time.sleep(.5)
        p=r.json()
        if not r.ok or p.get("status")!="success":raise ValueError(str(r.status_code)+":"+str([e.get("errorCode") for e in p.get("errors",[]) if isinstance(e,dict)]))
        return p["data"]
    universe,_=load_instruments();report=[]
    signals=json.loads(Path("pe_signals.json").read_text())
    assert len(signals)==135,"Unexpected signal count"
    for sig in signals:
        try:
            sym,day,minute,side=[sig[k] for k in ("symbol","date","minute","side")]
            key=universe[sym]
            rows=json.loads(gzip.decompress((root/m["stocks"][sym]["file"]).read_bytes()))["sessions"][day]
            spot=float(rows[str(minute-1)][3])
            expiry=min(e for e in get("/v2/expired-instruments/expiries",{"instrument_key":key}) if e>=day)
            contracts=get("/v2/expired-instruments/option/contract",{"instrument_key":key,"expiry_date":expiry})
            c=min([c for c in contracts if c.get("instrument_type")==side and c.get("expiry")==expiry and c.get("underlying_key")==key],key=lambda c:abs(float(c["strike_price"])-spot))
            raw=get("/v2/expired-instruments/historical-candle/"+quote(c["instrument_key"],safe="")+"/1minute/"+day+"/"+day)["candles"]
            bars={}
            for x in raw:
                t=datetime.fromisoformat(x[0]).astimezone(ZoneInfo("Asia/Kolkata"))
                if t.date().isoformat()!=day or t.second:continue
                v=list(map(float,x[1:7]))
                if len(v)==6 and all(map(math.isfinite,v)) and min(v[:4])>0 and v[1]>=max(v[0],v[2],v[3]) and v[2]<=min(v[0],v[1],v[3]):bars[t.hour*60+t.minute]=v
            window=[bars[i] for i in range(minute,minute+15)]
            if window[0][4]<=0 or window[-1][4]<=0:raise ValueError("No volume at entry or exit")
            entry=window[0][0];exit=window[-1][3]
            rec={**sig,"expiry":expiry,"strike":c["strike_price"],"entry":entry,"exit":exit,"gross_return_pct":100*(exit/entry-1),"net_proxy_pct":100*(exit*.995/(entry*1.005)-1),"mfe_pct":100*(max(x[1] for x in window)/entry-1),"mae_pct":100*(min(x[2] for x in window)/entry-1)}
            report.append(rec)
        except Exception as e:report.append({**sig,"error":str(e)})
    (out/"report.json").write_text(json.dumps(report,indent=2));print("OPTION_REPLAY",json.dumps(report),flush=True)
    if not any("entry" in x for x in report):raise SystemExit(1)
if __name__=="__main__":main()
