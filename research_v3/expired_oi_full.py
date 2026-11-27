import os,json,time,math
from pathlib import Path
from datetime import date,datetime,timedelta
from zoneinfo import ZoneInfo
from urllib.parse import quote
import requests
from live_market_engine import load_instruments
DAYS="2026-07-30 2026-07-31 2026-08-13 2026-08-14 2026-08-17 2026-08-18 2026-08-19 2026-08-20 2026-08-21 2026-08-24 2026-08-25 2026-08-26 2026-08-27 2026-08-28 2026-08-31 2026-09-09 2026-09-15 2026-09-16 2026-09-17 2026-09-18 2026-09-21 2026-09-22 2026-09-23 2026-09-24 2026-09-25 2026-09-28 2026-09-29 2026-09-30 2026-10-13 2026-10-14 2026-10-15 2026-10-16 2026-10-19 2026-10-21 2026-10-22 2026-10-23 2026-10-26 2026-10-27 2026-10-28 2026-10-29 2026-10-30 2026-11-11 2026-11-13 2026-11-16 2026-11-17 2026-11-18 2026-11-19 2026-11-20 2026-11-23 2026-11-25 2026-11-26".split()
def main():
    root=Path("research_output/expired_oi_full");root.mkdir(parents=True,exist_ok=True)
    report={"requested_dates":DAYS,"stocks":{},"errors":[]}
    session=requests.Session()
    session.headers.update({"Authorization":"Bearer "+os.environ["UPSTOX_ACCESS_TOKEN"],"Accept":"application/json"})
    def get(path,params=None):
        for attempt in range(3):
            r=session.get("https://api.upstox.com"+path,params=params,timeout=40)
            if r.status_code not in (429,500,502,503,504):break
            time.sleep(2**attempt)
        time.sleep(.4)
        p=r.json()
        if not r.ok or p.get("status")!="success":
            raise ValueError(str(r.status_code)+":"+str([e.get("errorCode") for e in p.get("errors",[]) if isinstance(e,dict)]))
        return p["data"]
    universe,active=load_instruments()
    symbols=sorted(universe)
    report["universe_count"]=len(symbols)
    for symbol in symbols:
        rows={}; meta={"contracts":[]};report["stocks"][symbol]=meta
        try:
            key=universe[symbol]
            expiries=sorted(set(get("/v2/expired-instruments/expiries",{"instrument_key":key})))
            current=active.get(symbol)
            if current:expiries=sorted(set(expiries+[current["expiry"]]))
            for expiry in sorted({next((e for e in expiries if e>=d),"") for d in DAYS}-{""}):
                wanted=[d for d in DAYS if next((e for e in expiries if e>=d),"")==expiry]
                try:
                    if current and expiry==current["expiry"]:
                        k=current["key"];prefix="/v3/historical-candle/"+quote(k,safe="")+"/minutes/1/"
                    else:
                        contracts=get("/v2/expired-instruments/future/contract",{"instrument_key":key,"expiry_date":expiry})
                        matches=[c for c in contracts if c.get("instrument_type")=="FUT" and c.get("expiry")==expiry and c.get("underlying_key")==key]
                        if len(matches)!=1:raise ValueError("ambiguous or missing contract")
                        k=matches[0]["instrument_key"];prefix="/v2/expired-instruments/historical-candle/"+quote(k,safe="")+"/1minute/"
                    meta["contracts"].append({"expiry":expiry,"key":k,"requested_dates":wanted})
                    start=date.fromisoformat(wanted[0]); end=date.fromisoformat(wanted[-1])
                    while start<=end:
                        stop=min(start+timedelta(days=20),end)
                        raw=get(prefix+str(stop)+"/"+str(start))["candles"]
                        for c in raw:
                            if len(c)<7:continue
                            t=datetime.fromisoformat(c[0]).astimezone(ZoneInfo("Asia/Kolkata"))
                            d=t.date().isoformat(); minute=t.hour*60+t.minute
                            if d not in wanted or not 555<=minute<930 or t.second:continue
                            try:v=list(map(float,c[1:7]))
                            except (ValueError,TypeError):continue
                            if not all(map(math.isfinite,v)) or v[5]<=0 or min(v[:4])<=0 or v[4]<0 or v[1]<max(v[0],v[2],v[3]) or v[2]>min(v[0],v[1],v[3]):continue
                            rows.setdefault(d,{})[str(minute)]=[expiry,*v]
                        start=stop+timedelta(days=1)
                except Exception as e:report["errors"].append({"symbol":symbol,"expiry":expiry,"error":str(e) if isinstance(e,ValueError) else type(e).__name__})
        except Exception as e:report["errors"].append({"symbol":symbol,"error":str(e) if isinstance(e,ValueError) else type(e).__name__})
        meta.update({"positive_oi_candles":sum(map(len,rows.values())),"complete_sessions":sum(len(x)==375 for x in rows.values()),"daily_counts":{d:len(rows.get(d,{})) for d in DAYS}})
        (root/(symbol+".json")).write_text(json.dumps(rows,allow_nan=False))
        (root/"manifest.json").write_text(json.dumps(report,indent=2))
        print(symbol,meta["complete_sessions"],meta["positive_oi_candles"],flush=True)
    report["complete_sessions"]=sum(x["complete_sessions"] for x in report["stocks"].values())
    report["expected_sessions"]=len(DAYS)*len(symbols)
    report["status"]="COMPLETE" if report["complete_sessions"]==report["expected_sessions"] and len(symbols)==218 and not report["errors"] else "PARTIAL" if report["complete_sessions"] else "FAILED"
    (root/"manifest.json").write_text(json.dumps(report,indent=2))
    print(json.dumps({"status":report["status"],"universe_count":len(symbols),"complete_sessions":report["complete_sessions"],"expected_sessions":report["expected_sessions"],"positive_oi_candles":sum(x["positive_oi_candles"] for x in report["stocks"].values()),"errors":report["errors"],"stocks":{s:{"sessions":m["complete_sessions"],"candles":m["positive_oi_candles"]} for s,m in report["stocks"].items()}}),flush=True)
    if report["status"]!="COMPLETE":raise SystemExit(1)
if __name__=="__main__":main()
