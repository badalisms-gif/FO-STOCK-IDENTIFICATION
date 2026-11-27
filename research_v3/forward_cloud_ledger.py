"""Server-only Supabase REST ledger; one writer, separate strategy records."""
import os,json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
from research_v3.forward_signal_ledger import MUTABLE,TERMINAL

class CloudLedger:
    def __init__(self):
        self.url=os.environ.get("SUPABASE_URL","https://axtwwftqsbiqhcfgmaxo.supabase.co").rstrip("/")+"/rest/v1/"
        self.http=requests.Session()
        key=os.environ["SUPABASE_SERVICE_ROLE_KEY"]
        self.http.headers.update({"apikey":key,"Content-Type":"application/json"})
        if not key.startswith("sb_secret_"): self.http.headers["Authorization"]="Bearer "+key
    def call(self,method,table,params=None,data=None,prefer=None):
        r=self.http.request(method,self.url+table,params=params,json=data,headers={"Prefer":prefer} if prefer else {},timeout=15)
        r.raise_for_status()
        return r.json() if r.content else None
    def register(self,row):
        strategies=["CE STRICT","CE A"] if row["strategy"]=="CE STRICT + CE A" else [row["strategy"]]
        for strategy in strategies:
            original=dict(row,strategy=strategy)
            payload={k:original[k] for k in ("session_date","signal_id","strategy","symbol","instrument_key","first_qualified_at","original_score","trade_status")}
            payload.update(original=original,current_state={k:v for k,v in original.items() if k in MUTABLE})
            self.call("POST","fo_signals",{"on_conflict":"session_date,signal_id,strategy"},payload,"resolution=ignore-duplicates,return=minimal")
    def raw(self,day,sid=None):
        params={"session_date":"eq."+day,"order":"signal_id,strategy"}
        if sid is not None:params["signal_id"]="eq."+sid
        result=[]
        for offset in range(0,100000,500):
            batch=self.call("GET","fo_signals",{**params,"limit":500,"offset":offset})
            result.extend(batch)
            if len(batch)<500:return result
        raise RuntimeError("ledger pagination limit")
    def rows(self,day):
        return [{**r["original"],**r["current_state"],"trade_status":r["trade_status"],"strategy":r["strategy"]} for r in self.raw(day)]
    def update(self,day,sid,changes):
        if set(changes)-MUTABLE:raise ValueError("immutable field")
        for r in self.raw(day,sid):
            patch=dict(changes)
            if r["trade_status"] in TERMINAL:
                patch={k:v for k,v in patch.items() if k in {"current_score","current_premium","bid","ask","filter_status","last_seen_at","spread_pct","oi","volume"}}
            status=patch.pop("trade_status",r["trade_status"])
            self.call("PATCH","fo_signals",{"session_date":"eq."+day,"signal_id":"eq."+sid,"strategy":"eq."+r["strategy"],"updated_at":"eq."+r["updated_at"]},{"current_state":{**r["current_state"],**patch},"trade_status":status},"return=representation")
    def export(self,day,path="forward_signals.json",scan_complete=False):
        stamp=datetime.now(ZoneInfo("Asia/Kolkata")).isoformat()
        self.call("POST","fo_scan_status",{"on_conflict":"session_date"},{"session_date":day,"observed_at":stamp,"scan_complete":scan_complete},"resolution=merge-duplicates,return=minimal")
        p=Path(path);tmp=p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"as_of":stamp,"session_date":day,"scan_complete":scan_complete,"signals":self.rows(day)},allow_nan=False));tmp.replace(p)
    def close(self):self.http.close()
