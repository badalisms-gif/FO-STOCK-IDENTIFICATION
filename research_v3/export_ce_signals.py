import json,gzip,hashlib
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score
from research_v3.detect import extract

def main():
    root=Path("research_output")
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
    dates=sorted(d.date.unique());assert len(dates)>=40
    train=d.date.isin(dates[:-20]);valid=d.date.isin(dates[-20:-10]);test=d.date.isin(dates[-10:])
    report={"exploratory":True,"target":"ATR multiples, next open entry, stop first, timeout miss","rows":len(d),"stocks":int(d.symbol.nunique()),"train_dates":dates[:-20],"validation_dates":dates[-20:-10],"test_dates":dates[-10:],"results":{}}
    exported=[]
    def alerts(mask,scores,t,label,export=False):
        a=d.loc[mask,["symbol","date","minute",label]].copy();a["score"]=scores
        direction=1 if "_up_" in label else -1
        gate=(direction*d.loc[mask,"ema_gap"]>0)&(direction*d.loc[mask,"vwap_gap"]>0)&(direction*d.loc[mask,"relative_15"]>0)&(d.loc[mask,"adx"]>=20)
        a=a[gate]
        last={};hits=n=0
        for sym,day,m,y,_ in a[a.score>=t].sort_values(["symbol","date","minute"]).itertuples(index=False,name=None):
            if m-last.get((sym,day),-1000)<int(label.split("_")[-1]):continue
            last[sym,day]=m;n+=1;hits+=int(y)
            if export:exported.append({"symbol":sym,"date":day,"minute":int(m),"side":"CE" if "_up_" in label else "PE","spot_hit":int(y)})
        return {"alerts":n,"hits":hits,"precision":hits/n if n else None,"false_alerts":n-hits}
    for name,features in [("strict_technical",base+technical),("strict_index",base+technical+index_features),("strict_index_oi",base+technical+index_features+extra)]:
        X=d[features].replace([np.inf,-np.inf],np.nan).astype("float32")
        report["results"][name]={}
        for label in labels:
            y=d[label].astype("int8")
            model=HistGradientBoostingClassifier(max_iter=80,max_leaf_nodes=15,min_samples_leaf=100,learning_rate=.08,random_state=42)
            model.fit(X.loc[train],y[train])
            pv=model.predict_proba(X.loc[valid])[:,1];pt=model.predict_proba(X.loc[test])[:,1]
            candidates=[{"threshold":float(t),**alerts(valid,pv,t,label)} for t in np.quantile(pv,[.99,.995,.998,.999,.9995])]
            eligible=[a for a in candidates if a["alerts"]>=20]
            chosen=max(eligible,key=lambda a:a["precision"]) if eligible else None
            result={"validation":candidates,"chosen":chosen,"test_ap":float(average_precision_score(y[test],pt)),"test_base_rate":float(y[test].mean()),"test":alerts(test,pt,chosen["threshold"],label,export=(name=="strict_index_oi" and label=="label_up_1_15")) if chosen else None}
            report["results"][name][label]=result
            print(name,label,json.dumps(result),flush=True)
    Path("ce_signals.json").write_text(json.dumps(exported,indent=2))
    Path("index_comparison.json").write_text(json.dumps(report,indent=2))
    print("COMPARISON",json.dumps(report),flush=True)
if __name__=="__main__":main()
