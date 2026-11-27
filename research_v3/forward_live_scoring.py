"""Causal frozen-model scoring on synchronized completed minute prefixes."""
import numpy as np
import pandas as pd
import sklearn
import joblib

def score_prefix(bundle,spots,futures,indices,minute):
    if bundle["sklearn_version"]!=sklearn.__version__: raise ValueError("model version mismatch")
    if not 586<=minute<=870: return []
    keys=[str(m) for m in range(555,minute)]
    def arr(rows,n):
        a=np.asarray([rows[k] for k in keys],dtype=float)
        if a.shape!=(len(keys),n) or not np.isfinite(a).all(): raise ValueError("incomplete prefix")
        return a
    frames=[]
    for symbol,rows in spots.items():
        try:
            a=arr(rows,5); b=arr(futures[symbol],6)
            if min(b[:,5])<=0: continue
        except (KeyError,ValueError): continue
        o,h,l,c,v=map(pd.Series,a.T)
        p=pd.Series(b[:,3]); oi=pd.Series(b[:,5])
        f=pd.DataFrame(index=c.index)
        for n in (1,5,10,15,30): f[f"return_{n}"]=c.pct_change(n)
        for n in (5,10,15):
            f[f"range_{n}"]=(h.rolling(n).max()-l.rolling(n).min())/c
            f[f"volume_ratio_{n}"]=v.rolling(n).mean()/v.rolling(n).mean().shift(n).replace(0,np.nan)
            f[f"turnover_proxy_ratio_{n}"]=(v*c).rolling(n).mean()/(v*c).rolling(n).mean().shift(n).replace(0,np.nan)
        f["oi_5"]=oi.pct_change(5); f["oi_15"]=oi.pct_change(15)
        f["fut_return_5"]=p.pct_change(5); f["basis"]=p/c-1
        f["buildup"]=np.sign(f.fut_return_5)*np.sign(f.oi_5)
        tr=pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
        atr=tr.ewm(alpha=1/14,adjust=False).mean()
        up=h.diff(); down=-l.diff()
        plus=up.where((up>down)&(up>0),0).ewm(alpha=1/14,adjust=False).mean()/atr
        minus=down.where((down>up)&(down>0),0).ewm(alpha=1/14,adjust=False).mean()/atr
        f["adx"]=100*((plus-minus).abs()/(plus+minus).replace(0,np.nan)).ewm(alpha=1/14,adjust=False).mean()
        f["atr_pct"]=atr/c
        f["vwap_gap"]=c/(((h+l+c)/3*v).cumsum()/v.cumsum().replace(0,np.nan))-1
        f["ema_gap"]=c.ewm(span=9,adjust=False).mean()/c.ewm(span=21,adjust=False).mean()-1
        f["breakout_gap"]=c/h.shift().rolling(20).max()-1
        f["breakdown_gap"]=c/l.shift().rolling(20).min()-1
        row=f.iloc[-1].to_dict();row.update(symbol=symbol,minute=minute,spot_price=float(c.iloc[-1]));frames.append(row)
    if len(frames)<.95*len(spots) or not frames: raise ValueError("insufficient synchronized stock coverage")
    d=pd.DataFrame(frames)
    d["market_return_15"]=d.return_15.median();d["relative_15"]=d.return_15-d.market_return_15
    d["breadth"]=(d.return_15>0).mean()
    ix={}
    for name,rows in indices.items():
        try: c=arr(rows,5)[:,3];ix[name]=c[-1]/c[-16]-1
        except (KeyError,ValueError): continue
    if "NIFTY" not in ix or len(ix)<12: raise ValueError("insufficient index coverage")
    sector=np.array([v for k,v in ix.items() if k!="NIFTY"])
    d["nifty_return"]=ix["NIFTY"];d["sector_median"]=np.median(sector);d["sector_breadth"]=(sector>0).mean()
    d["relative_nifty"]=d.return_15-d.nifty_return
    d["score"]=bundle["model"].predict_proba(d[bundle["features"]].replace([np.inf,-np.inf],np.nan).astype("float32"))[:,1]
    d["technical_gate"]=(d.ema_gap>0)&(d.vwap_gap>0)&(d.relative_15>0)&(d.adx>=20)
    return d[["symbol","minute","spot_price","score","technical_gate"]].to_dict("records")
