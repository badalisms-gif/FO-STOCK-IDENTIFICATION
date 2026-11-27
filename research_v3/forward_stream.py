"""Upstox V3 SDK streaming adapter, read-only.
Use exchange-provided I1 candles, not sampled LTP as OHLC.
SDK payload and subscription capacity require market-hours verification.
"""
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
import math
IST=ZoneInfo("Asia/Kolkata")
class StreamBuffer:
    def __init__(self, instruments):
        self.instruments=instruments
        self.lock=threading.RLock()
        self.rows={"spot":{},"future":{},"index":{}}
        self.connected=False
        self.errors=0
    def ingest(self,message):
        with self.lock:
            for key,feed in message.get("feeds",{}).items():
                if key not in self.instruments:continue
                kind,symbol=self.instruments[key]
                full=feed.get("fullFeed",{})
                data=full.get("marketFF") or full.get("indexFF") or {}
                for c in data.get("marketOHLC",{}).get("ohlc",[]):
                    if c.get("interval")!="I1":continue
                    try:
                        t=datetime.fromtimestamp(int(c["ts"])/1000,IST)
                        if t.date()!=datetime.now(IST).date():continue
                        m=t.hour*60+t.minute
                        if not 555<=m<930:continue
                        row=[float(c[k]) for k in ("open","high","low","close")]+[float(c.get("vol",0))]
                        if not all(map(math.isfinite,row)) or min(row[:4])<=0:continue
                        # Feed OI is a current snapshot, not historical bar OI.
                        # Do not attach it to an earlier candle.
                        if kind=="future":continue
                        self.rows[kind].setdefault(symbol,{})[str(m)]=row
                    except (KeyError,ValueError,TypeError,OverflowError):self.errors+=1
    def completed(self,minute):
        with self.lock:
            return {k:{s:{m:list(v) for m,v in rows.items() if int(m)<minute} for s,rows in symbols.items()} for k,symbols in self.rows.items()}
def connect(token,buffer):
    import upstox_client
    config=upstox_client.Configuration()
    config.access_token=token
    stream=upstox_client.MarketDataStreamerV3(upstox_client.ApiClient(config),list(buffer.instruments),"full")
    def opened(*args):buffer.connected=True
    def closed(*args):buffer.connected=False
    stream.on("open",opened)
    stream.on("close",closed)
    stream.on("message",buffer.ingest)
    stream.connect()
    return stream
