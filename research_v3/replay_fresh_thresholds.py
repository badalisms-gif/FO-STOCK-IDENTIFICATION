import os,json,time,gzip,math
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from urllib.parse import quote
import requests
from live_market_engine import load_instruments
def main():
    root=Path("fresh_data");out=Path("premium_grid");out.mkdir(exist_ok=True)
    m=json.loads((root/"manifest.json").read_text())
    session=requests.Session();session.headers.update({"Authorization":"Bearer "+os.environ["UPSTOX_ACCESS_TOKEN"],"Accept":"application/json"})
    def get(path,params=None):
        r=session.get("https://api.upstox.com"+path,params=params,timeout=40);time.sleep(.5)
        p=r.json()
        if not r.ok or p.get("status")!="success":raise ValueError(str(r.status_code)+":"+str([e.get("errorCode") for e in p.get("errors",[]) if isinstance(e,dict)]))
        return p["data"]
    universe,_=load_instruments();report=[]
    groups=json.loads(Path("ce_threshold_signals.json").read_text())
    signals=list({(x["symbol"],x["date"],x["minute"]):x for g in groups for x in g["signals"]}.values())
    stocks={x["symbol"]:x for x in m["coverage"] if x["kind"]=="spot"}
    for sig in signals:
        try:
            sym,day,minute,side=[sig[k] for k in ("symbol","date","minute","side")]
            key=universe[sym]
            rows=json.loads(gzip.decompress((root/stocks[sym]["file"]).read_bytes()))["rows"]
            spot=float(rows[str(minute-1)][3])
            expired=get("/v2/expired-instruments/expiries",{"instrument_key":key})
            active=get("/v2/option/contract",{"instrument_key":key})
            expiry=min(e for e in set(expired)|{c["expiry"] for c in active} if e>=day)
            is_expired=expiry in expired
            contracts=get("/v2/expired-instruments/option/contract",{"instrument_key":key,"expiry_date":expiry}) if is_expired else active
            c=min([c for c in contracts if c.get("instrument_type")==side and c.get("expiry")==expiry and c.get("underlying_key")==key],key=lambda c:abs(float(c["strike_price"])-spot))
            ik=quote(c["instrument_key"],safe="")
            endpoint="/v2/expired-instruments/historical-candle/"+ik+"/1minute/" if is_expired else "/v3/historical-candle/"+ik+"/minutes/1/"
            raw=get(endpoint+day+"/"+day)["candles"]
            if not is_expired and len(raw)<375 and day==datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat():
                raw=get("/v3/historical-candle/intraday/"+ik+"/minutes/1")["candles"]
            bars={}
            for x in raw:
                t=datetime.fromisoformat(x[0]).astimezone(ZoneInfo("Asia/Kolkata"))
                if t.date().isoformat()!=day or t.second or not 555<=t.hour*60+t.minute<=929:continue
                v=list(map(float,x[1:7]))
                if len(v)==6 and all(map(math.isfinite,v)) and min(v[:4])>0 and v[1]>=max(v[0],v[2],v[3]) and v[2]<=min(v[0],v[1],v[3]):bars[t.hour*60+t.minute]=v
            window=[bars[i] for i in range(minute,minute+15)]
            if window[0][4]<=0 or window[-1][4]<=0:raise ValueError("No volume at entry or exit")
            entry=window[0][0];exit=window[-1][3]
            rec={**sig,"expiry":expiry,"strike":c["strike_price"],"entry":entry,"exit":exit,"gross_return_pct":100*(exit/entry-1),"net_proxy_pct":100*(exit*.995/(entry*1.005)-1),"mfe_pct":100*(max(x[1] for x in window)/entry-1),"mae_pct":100*(min(x[2] for x in window)/entry-1)}
            rec["volume_entry"]=window[0][4]
            rec["volume_exit"]=window[-1][4]
            rec["grid"]={}
            for sl in (.08,.10,.15):
                for tp in (.15,.20,.30):
                    if tp/sl<1.5:continue
                    stop=entry*(1-sl);target=entry*(1+tp);price=exit;reason="timeout"
                    for b in window:
                        if b[0]<=stop:price=b[0];reason="stop_gap";break
                        if b[2]<=stop:price=stop;reason="stop";break
                        if b[0]>=target:price=target;reason="target";break
                        if b[1]>=target:price=target;reason="target";break
                    rec["grid"][f"{sl}:{tp}"]={"return":100*(price*.995/(entry*1.005)-1),"reason":reason}
            report.append(rec)
        except Exception as e:report.append({**sig,"error":str(e)})
    (out/"report.json").write_text(json.dumps(report,indent=2))
    summary=[]
    lookup={(r["symbol"],r["date"],r["minute"]):r for r in report}
    for g in groups:
        a=[lookup[(x["symbol"],x["date"],x["minute"])] for x in g["signals"]]
        valid=[r for r in a if "grid" in r and r["entry"]>=5 and r["date"]!=r["expiry"]]
        returns=[r["grid"]["0.08:0.3"]["return"] for r in valid]
        summary.append({"threshold":g["threshold"],"signals":len(a),"valid":len(valid),"errors":sum("error" in r for r in a),"excluded_policy":sum("grid" in r for r in a)-len(valid),"wins":sum(x>0 for x in returns),"mean_net_proxy_pct":sum(returns)/len(returns) if returns else None,"worst":min(returns) if returns else None})
    (out/"summary.json").write_text(json.dumps(summary,indent=2))
    print("PREMIUM_THRESHOLD_SUMMARY",json.dumps(summary),flush=True)
if __name__=="__main__":main()
