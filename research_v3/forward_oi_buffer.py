"""Conservative futures OI capture from timestamped stream observations.
Only observations within a completed candle's own minute are eligible.
Not an exchange-certified historical OI close.
"""
from datetime import datetime
from zoneinfo import ZoneInfo
import math
IST=ZoneInfo("Asia/Kolkata")
class OIBuffer:
    def __init__(self):self.values={}
    def ingest(self,key,oi,source_ms,received):
        try:
            t=datetime.fromtimestamp(int(source_ms)/1000,IST)
            oi=float(oi)
            if not math.isfinite(oi) or oi<=0 or not 0<=(received-t).total_seconds()<=5:return False
            minute=t.hour*60+t.minute
            if not 555<=minute<930:return False
            k=(t.date().isoformat(),key,minute)
            old=self.values.get(k)
            if old is None or t>old[0]:self.values[k]=(t,oi)
            return True
        except (ValueError,TypeError,OverflowError):return False
    def attach(self,day,key,minute,candle):
        item=self.values.get((day,key,minute))
        if item is None or item[0].second<55:return None
        return [*candle,item[1]]
