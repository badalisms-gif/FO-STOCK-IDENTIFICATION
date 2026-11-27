"""Price/volume research only; no execution or option premium claims."""
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
    features=["return_1","return_5","return_10","return_15","return_30","range_5","range_10","range_15","volume_ratio_5","volume_ratio_10","volume_ratio_15","turnover_proxy_ratio_5","turnover_proxy_ratio_10","turnover_proxy_ratio_15","minute"]
    labels=["label_up_1_15","label_down_1_15","label_up_2_30","label_down_2_30"]
    frames=[]
    for symbol,meta in manifest["stocks"].items():
        blob=(root/meta["file"]).read_bytes()
        assert hashlib.sha256(blob).hexdigest()==meta["sha256"]
        data=json.loads(gzip.decompress(blob))
        for day,rows in sorted(data["sessions"].items()):
            f=extract(symbol,day,rows)
            frames.append(f[["symbol","date"]+features+labels].iloc[30:345].copy())
    d=pd.concat(frames,ignore_index=True)
    del frames
    dates=sorted(d.date.unique())
    assert len(dates)>=40
    train=d.date.isin(dates[:-20])
    valid=d.date.isin(dates[-20:-10])
    test=d.date.isin(dates[-10:])
    X=d[features].replace([np.inf,-np.inf],np.nan).astype("float32")
    report={"train_dates":dates[:-20],"validation_dates":dates[-20:-10],"test_dates":dates[-10:],"features":features,"results":{}}
    def alerts(mask,scores,threshold,horizon,label):
        a=d.loc[mask,["symbol","date","minute",label]].copy()
        a["score"]=scores
        a=a[a.score>=threshold].sort_values(["symbol","date","minute"])
        last={}
        hits=0;n=0
        for symbol,day,minute,y,score in a.itertuples(index=False,name=None):
            key=(symbol,day)
            if minute-last.get(key,-1000)<horizon:continue
            last[key]=minute;n+=1;hits+=int(y)
        return {"alerts":n,"hits":hits,"false_alerts":n-hits,"precision":hits/n if n else None,"alerts_per_day":n/int(d.loc[mask,"date"].nunique())}
    for label in labels:
        horizon=int(label.split("_")[-1])
        y=d[label].astype("int8")
        model=HistGradientBoostingClassifier(max_iter=80,max_leaf_nodes=15,min_samples_leaf=100,learning_rate=.08,random_state=42)
        model.fit(X.loc[train],y[train])
        pv=model.predict_proba(X.loc[valid])[:,1]
        pt=model.predict_proba(X.loc[test])[:,1]
        candidates=[]
        for q in [.99,.995,.998,.999,.9995]:
            t=float(np.quantile(pv,q))
            a=alerts(valid,pv,t,horizon,label)
            candidates.append({"threshold":t,**a})
        eligible=[a for a in candidates if a["alerts"]>=20]
        chosen=max(eligible,key=lambda a:a["precision"]) if eligible else None
        result={"validation_candidates":candidates,"test_base_rate":float(y[test].mean()),"test_average_precision":float(average_precision_score(y[test],pt)),"chosen":chosen}
        if chosen:
            result["test"]=alerts(test,pt,chosen["threshold"],horizon,label)
        report["results"][label]=result
        print(label,json.dumps(result),flush=True)
    (root/"pattern_report.json").write_text(json.dumps(report,indent=2))
    print("PATTERN_REPORT",json.dumps(report),flush=True)
if __name__=="__main__":main()
