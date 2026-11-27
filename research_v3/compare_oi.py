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
            frames.append(f[["symbol","date"]+base+extra+labels].iloc[30:345].copy())
    d=pd.concat(frames,ignore_index=True);del frames
    dates=sorted(d.date.unique());assert len(dates)>=40
    train=d.date.isin(dates[:-20]);valid=d.date.isin(dates[-20:-10]);test=d.date.isin(dates[-10:])
    report={"rows":len(d),"stocks":int(d.symbol.nunique()),"train_dates":dates[:-20],"validation_dates":dates[-20:-10],"test_dates":dates[-10:],"results":{}}
    def alerts(mask,scores,t,label):
        a=d.loc[mask,["symbol","date","minute",label]].copy();a["score"]=scores
        last={};hits=n=0
        for sym,day,m,y,_ in a[a.score>=t].sort_values(["symbol","date","minute"]).itertuples(index=False,name=None):
            if m-last.get((sym,day),-1000)<int(label.split("_")[-1]):continue
            last[sym,day]=m;n+=1;hits+=int(y)
        return {"alerts":n,"hits":hits,"precision":hits/n if n else None,"false_alerts":n-hits}
    for name,features in [("baseline",base),("futures_oi",base+extra)]:
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
            result={"validation":candidates,"chosen":chosen,"test_ap":float(average_precision_score(y[test],pt)),"test_base_rate":float(y[test].mean()),"test":alerts(test,pt,chosen["threshold"],label) if chosen else None}
            report["results"][name][label]=result
            print(name,label,json.dumps(result),flush=True)
    Path("comparison.json").write_text(json.dumps(report,indent=2))
    print("COMPARISON",json.dumps(report),flush=True)
if __name__=="__main__":main()
