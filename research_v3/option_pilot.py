import os,json,time,gzip
from pathlib import Path
from urllib.parse import quote
import requests
from live_market_engine import load_instruments
def main():
    root=Path("research_output");out=Path("option_pilot");out.mkdir(exist_ok=True)
    m=json.loads((root/"manifest.json").read_text())
    session=requests.Session();session.headers.update({"Authorization":"Bearer "+os.environ["UPSTOX_ACCESS_TOKEN"],"Accept":"application/json"})
    def get(path,params=None):
        r=session.get("https://api.upstox.com"+path,params=params,timeout=40);time.sleep(.5)
        p=r.json()
        if not r.ok or p.get("status")!="success":raise ValueError(str(r.status_code)+":"+str([e.get("errorCode") for e in p.get("errors",[]) if isinstance(e,dict)]))
        return p["data"]
    universe,_=load_instruments();report=[]
    for sym in ["RELIANCE","HDFCBANK","INFY","SBIN","TCS"]:
        try:
            key=universe[sym];day="2026-11-20"
            rows=json.loads(gzip.decompress((root/m["stocks"][sym]["file"]).read_bytes()))["sessions"][day]
            spot=float(rows["555"][3])
            expiry=min(e for e in get("/v2/expired-instruments/expiries",{"instrument_key":key}) if e>=day)
            contracts=get("/v2/expired-instruments/option/contract",{"instrument_key":key,"expiry_date":expiry})
            for side in ["CE","PE"]:
                try:
                    matches=[c for c in contracts if c.get("instrument_type")==side and c.get("expiry")==expiry and c.get("underlying_key")==key]
                    c=min(matches,key=lambda c:abs(float(c["strike_price"])-spot))
                    k=c["instrument_key"]
                    candles=get("/v2/expired-instruments/historical-candle/"+quote(k,safe="")+"/1minute/"+day+"/"+day)["candles"]
                    record={"symbol":sym,"side":side,"day":day,"expiry":expiry,"strike":c["strike_price"],"spot_reference":spot,"contract":k,"candles":len(candles),"positive_volume":sum(float(x[5])>0 for x in candles if len(x)>5),"positive_oi":sum(float(x[6])>0 for x in candles if len(x)>6),"fields":len(candles[0]) if candles else 0}
                    (out/(sym+"_"+side+".json")).write_text(json.dumps({"meta":record,"candles":candles}))
                    report.append(record)
                except Exception as e:report.append({"symbol":sym,"side":side,"error":str(e)})
        except Exception as e:report.append({"symbol":sym,"error":str(e)})
    (out/"manifest.json").write_text(json.dumps(report,indent=2));print("OPTION_PILOT",json.dumps(report),flush=True)
    if len(report)!=10 or any("error" in x or not x.get("candles") for x in report):raise SystemExit(1)
if __name__=="__main__":main()
