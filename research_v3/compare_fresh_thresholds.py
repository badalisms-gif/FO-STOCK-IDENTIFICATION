import json,gzip,hashlib,joblib,sklearn
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score
from research_v3.detect import extract

def prepare():
    fresh=Path("fresh_data")
    m=json.loads((fresh/"manifest.json").read_text())
    day=m["date"];assert day>"2026-11-26"
    root=Path("research_output");root.mkdir(exist_ok=True)
    ctx=Path("context");ctx.mkdir(exist_ok=True)
    oi=Path("oi");oi.mkdir(exist_ok=True)
    stocks={};coverage=[]
    for meta in m["coverage"]:
        if meta["rows"]!=375:continue
        blob=(fresh/meta["file"]).read_bytes()
        assert hashlib.sha256(blob).hexdigest()==meta["sha256"]
        p=json.loads(gzip.decompress(blob));symbol=p["symbol"];kind=p["kind"]
        if kind=="future":
            (oi/(symbol+".json")).write_text(json.dumps({day:{k:[p["expiry"],*v] for k,v in p["rows"].items()}}))
            continue
        b=gzip.compress(json.dumps({"sessions":{day:p["rows"]}}).encode())
        dest=root if kind=="spot" else ctx
        (dest/meta["file"]).write_bytes(b)
        entry={"file":meta["file"],"sha256":hashlib.sha256(b).hexdigest(),"kind":kind,"symbol":symbol}
        if kind=="spot":stocks[symbol]=entry
        else:coverage.append(entry)
    (root/"manifest.json").write_text(json.dumps({"stocks":stocks}))
    (ctx/"manifest.json").write_text(json.dumps({"coverage":coverage}))

def main():
    root=Path("research_output")
    prepare()
    manifest=json.loads((root/"manifest.json").read_text())
    base=["return_1","return_5","return_10","return_15","return_30","range_5","range_10","range_15","volume_ratio_5","volume_ratio_10","volume_ratio_15","turnover_proxy_ratio_5","turnover_proxy_ratio_10","turnover_proxy_ratio_15","minute"]
    extra=["oi_5","oi_15","fut_return_5","basis","buildup"]
    labels=["label_up_1_15","label_down_1_15","label_up_2_30","label_down_2_30"]
    technical=["vwap_gap","ema_gap","atr_pct","adx","breakout_gap","breakdown_gap","market_return_15","relative_15","breadth"]
    ctx=Path("context")
    cm=json.loads((ctx/"manifest.json").read_text())
    indices={}
    for meta in cm["coverage"]:
        if meta["kind"]!="index":continue
        blob=(ctx/meta["file"]).read_bytes()
        assert hashlib.sha256(blob).hexdigest()==meta["sha256"]
        indices[meta["symbol"]]=json.loads(gzip.decompress(blob))["sessions"]
    assert "NIFTY" in indices
    frames=[]
    for symbol,meta in manifest["stocks"].items():
        path=Path("oi")/(symbol+".json")
        if not path.exists():continue
        oi=json.loads(path.read_text())
        blob=(root/meta["file"]).read_bytes()
        assert hashlib.sha256(blob).hexdigest()==meta["sha256"]
        for day,rows in json.loads(gzip.decompress(blob))["sessions"].items():
            r=oi.get(day,{})
            if len(r)!=375 or len({v[0] for v in r.values()})!=1:continue
            a=np.asarray([r[str(m)][1:] for m in range(555,930)],dtype=float)
            f=extract(symbol,day,rows)
            o=pd.Series(a[:,5]);p=pd.Series(a[:,3])
            f["oi_5"]=o.pct_change(5);f["oi_15"]=o.pct_change(15)
            f["fut_return_5"]=p.pct_change(5)
            f["basis"]=p/f.close-1
            f["buildup"]=np.sign(f.fut_return_5)*np.sign(f.oi_5)
            spot=np.asarray([rows[str(m)] for m in range(555,930)],dtype=float)
            h,l,c,v=map(pd.Series,[spot[:,1],spot[:,2],spot[:,3],spot[:,4]])
            tr=pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
            atr=tr.ewm(alpha=1/14,adjust=False).mean()
            up=h.diff();down=-l.diff()
            plus=up.where((up>down)&(up>0),0).ewm(alpha=1/14,adjust=False).mean()/atr
            minus=down.where((down>up)&(down>0),0).ewm(alpha=1/14,adjust=False).mean()/atr
            f["adx"]=100*((plus-minus).abs()/(plus+minus).replace(0,np.nan)).ewm(alpha=1/14,adjust=False).mean()
            f["atr_pct"]=atr/c
            f["vwap_gap"]=c/(((h+l+c)/3*v).cumsum()/v.cumsum().replace(0,np.nan))-1
            f["ema_gap"]=c.ewm(span=9,adjust=False).mean()/c.ewm(span=21,adjust=False).mean()-1
            f["breakout_gap"]=c/h.shift().rolling(20).max()-1
            f["breakdown_gap"]=c/l.shift().rolling(20).min()-1
            # Entry at next bar open; conservative stop-first if both touched.
            for side in ("up","down"):
                sign=1 if side=="up" else -1
                for mult,horizon in [(1,15),(2,30)]:
                    outcome=np.zeros(375,dtype=np.int8)
                    for i in range(30,345):
                        entry=spot[i+1,0]; risk=float(atr.iloc[i])
                        if not np.isfinite(risk) or risk<=0:continue
                        target=entry+sign*mult*risk; stop=entry-sign*risk
                        for j in range(i+1,min(i+1+horizon,375)):
                            adverse=spot[j,2]<=stop if sign==1 else spot[j,1]>=stop
                            favorable=spot[j,1]>=target if sign==1 else spot[j,2]<=target
                            if adverse:break
                            if favorable:outcome[i]=1;break
                    f[f"label_{side}_{mult}_{horizon}"]=outcome
            frames.append(f[["symbol","date"]+base+extra+technical[:6]+labels].iloc[30:345].copy())
    d=pd.concat(frames,ignore_index=True);del frames
    g=d.groupby(["date","minute"])["return_15"]
    d["market_return_15"]=g.transform("median")
    d["relative_15"]=d.return_15-d.market_return_15
    d["breadth"]=(d.return_15>0).groupby([d.date,d.minute]).transform("mean")
    context_frames=[]
    for name,sessions in indices.items():
        for day,rows in sessions.items():
            c=pd.Series([rows[str(m)][3] for m in range(555,930)])
            context_frames.append(pd.DataFrame({"date":day,"minute":np.arange(556,931),"index":name,"r":c.pct_change(15)}))
    ix=pd.concat(context_frames,ignore_index=True)
    nifty=ix[ix["index"]=="NIFTY"][["date","minute","r"]].rename(columns={"r":"nifty_return"})
    sector=ix[ix["index"]!="NIFTY"].groupby(["date","minute"]).r.agg(sector_median="median",sector_breadth=lambda x:(x>0).mean()).reset_index()
    d=d.merge(nifty,on=["date","minute"],how="inner",validate="many_to_one").merge(sector,on=["date","minute"],how="inner",validate="many_to_one")
    d["relative_nifty"]=d.return_15-d.nifty_return
    index_features=["nifty_return","sector_median","sector_breadth","relative_nifty"]
    bundle=joblib.load("frozen_ce_model.joblib")
    assert bundle["sklearn_version"]==sklearn.__version__
    assert min(d.date)>max(bundle["validation_dates"])
    X=d[bundle["features"]].replace([np.inf,-np.inf],np.nan).astype("float32")
    d["score"]=bundle["model"].predict_proba(X)[:,1]
    results=[]; exports=[]
    for threshold in [bundle["threshold"],.60,.58,.55,.52]:
        a=d[(d.score>=threshold)&(d.ema_gap>0)&(d.vwap_gap>0)&(d.relative_15>0)&(d.adx>=20)&(d.minute<=870)]
        last={};signals=[]
        for r in a.sort_values(["symbol","date","minute"]).itertuples():
            k=(r.symbol,r.date)
            if r.minute-last.get(k,-1000)<15:continue
            last[k]=r.minute
            signals.append({"symbol":r.symbol,"date":r.date,"minute":int(r.minute),"side":"CE","score":float(r.score),"spot_hit":int(r.label_up_1_15)})
        hits=sum(x["spot_hit"] for x in signals)
        results.append({"threshold":threshold,"alerts":len(signals),"hits":hits,"precision":hits/len(signals) if signals else None,"stocks":len({x["symbol"] for x in signals}),"days":len({x["date"] for x in signals})})
        exports.append({"threshold":threshold,"signals":signals})
    Path("ce_threshold_signals.json").write_text(json.dumps(exports,indent=2))
    Path("ce_threshold_summary.json").write_text(json.dumps(results,indent=2))
    print("THRESHOLD_SUMMARY",json.dumps(results),flush=True)
if __name__=="__main__":main()
