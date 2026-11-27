"""Offline research only. Labels use future closes; features use completed bars only."""
import gzip
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd

def extract(symbol, day, records):
    a = np.asarray([records[str(m)] for m in range(555,930)], dtype=float)
    if a.shape != (375,5) or not np.isfinite(a).all():
        raise ValueError("Invalid session")
    o,h,l,c,v = a.T
    p = pd.Series(c)
    vol = pd.Series(v)
    out = pd.DataFrame({"symbol":symbol,"date":day,"minute":np.arange(556,931),"close":c})
    for n in (1,5,10,15,30):
        out[f"return_{n}"] = p.pct_change(n)
    for n in (5,10,15):
        out[f"range_{n}"] = (pd.Series(h).rolling(n).max()-pd.Series(l).rolling(n).min())/p
        out[f"volume_ratio_{n}"] = vol.rolling(n).mean()/vol.rolling(n).mean().shift(n).replace(0,np.nan)
        out[f"turnover_proxy_ratio_{n}"] = (vol*p).rolling(n).mean()/(vol*p).rolling(n).mean().shift(n).replace(0,np.nan)
    for horizon in (5,10,15,30,60):
        future = np.full((375,horizon),np.nan)
        for k in range(1,horizon+1):
            future[:-k,k-1] = c[k:]/c[:-k]-1
        valid = np.arange(375)+horizon<375
        up = np.full(375,np.nan)
        down = up.copy()
        up[valid] = future[valid].max(axis=1)
        down[valid] = -future[valid].min(axis=1)
        out[f"future_up_{horizon}"] = up
        out[f"future_down_{horizon}"] = down
        for threshold in (1,2,3):
            for side,values in (("up",up),("down",down)):
                out[f"label_{side}_{threshold}_{horizon}"] = np.where(valid,values>=threshold/100,np.nan)
    return out

def main():
    root=Path("research_output")
    manifest=json.loads((root/"manifest.json").read_text())
    target=root/"features"
    target.mkdir(exist_ok=True)
    for symbol,meta in manifest["stocks"].items():
        blob=(root/meta["file"]).read_bytes()
        if hashlib.sha256(blob).hexdigest()!=meta["sha256"]:
            raise ValueError("Archive hash mismatch")
        data=json.loads(gzip.decompress(blob))
        frames=[extract(symbol,day,rows) for day,rows in sorted(data["sessions"].items())]
        if frames:
            pd.concat(frames,ignore_index=True).to_csv(target/(meta["file"]+".csv.gz"),index=False)
    print("Feature extraction complete; overlapping labels are not independent events.")

if __name__=="__main__":
    main()
