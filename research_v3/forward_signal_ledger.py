"""Durable local research ledger. One writer; refreshes never delete signals."""
import sqlite3,json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

IMMUTABLE={"session_date","signal_id","instrument_key","side","first_qualified_at","original_score","entry","stop_loss","target","expiry","lot_size","exit_deadline"}
TERMINAL={"TARGET HIT","SL HIT","TIME EXIT","UNPRICED EXIT","REJECTED"}
MUTABLE={"current_score","current_premium","ltp","bid","ask","spread_pct","oi","volume","delta","gamma","theta","vega","iv","last_seen_at","last_qualified_at","filter_status","trade_status","paper_pnl","exit_price","exit_reason","gross_pnl","estimated_charges","net_pnl"}

class Ledger:
    def __init__(self,path="forward_ledger.sqlite3"):
        self.db=sqlite3.connect(path,timeout=20)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS signals (day TEXT, id TEXT, payload TEXT NOT NULL, PRIMARY KEY(day,id))")
        self.db.commit()
    def register(self,row):
        day=row["session_date"]; sid=row["signal_id"]
        if not row.get("instrument_key") or not sid: raise ValueError("missing identity")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO signals VALUES (?,?,?)",(day,sid,json.dumps(row,allow_nan=False)))
    def update(self,day,sid,changes):
        if set(changes)-MUTABLE: raise ValueError("immutable or unknown field")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            old=self.db.execute("SELECT payload FROM signals WHERE day=? AND id=?",(day,sid)).fetchone()
            if old is None: raise KeyError(sid)
            row=json.loads(old[0])
            if row.get("trade_status") in TERMINAL:
                changes={k:v for k,v in changes.items() if k not in {"trade_status","paper_pnl","exit_price","exit_reason","gross_pnl","estimated_charges","net_pnl"}}
            row.update(changes)
            self.db.execute("UPDATE signals SET payload=? WHERE day=? AND id=?",(json.dumps(row,allow_nan=False),day,sid))
    def rows(self,day):
        return [json.loads(x[0]) for x in self.db.execute("SELECT payload FROM signals WHERE day=? ORDER BY id",(day,))]
    def export(self,day,path="forward_signals.json",scan_complete=False):
        p=Path(path); tmp=p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"as_of":datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),"session_date":day,"scan_complete":scan_complete,"signals":self.rows(day)},allow_nan=False))
        tmp.replace(p)
    def close(self): self.db.close()
