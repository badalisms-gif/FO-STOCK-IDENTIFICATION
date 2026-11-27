"""Paper execution only. Caller supplies timestamped live signals and quotes."""
from dataclasses import dataclass
from datetime import datetime, timedelta
import math

@dataclass(frozen=True)
class Quote:
    timestamp: datetime
    bid: float
    ask: float
    bid_size: int
    ask_size: int

def valid(q, now):
    return (q.timestamp.tzinfo is not None and now.tzinfo is not None
            and 0 <= (now-q.timestamp).total_seconds() <= 5
            and all(math.isfinite(x) for x in (q.bid,q.ask))
            and 0 < q.bid <= q.ask and q.bid_size > 0 and q.ask_size > 0)

def enter(signal_time, now, q, expiry, lot_size):
    if signal_time.tzinfo is None or now.tzinfo is None:
        return None, "timezone_required"
    if not 0 <= (now-signal_time).total_seconds() <= 60:
        return None, "stale_signal"
    if not valid(q,now) or q.timestamp < signal_time:
        return None, "invalid_quote"
    if now.hour*60+now.minute > 870 or expiry <= now.date():
        return None, "policy_exclusion"
    if q.ask < 5 or (q.ask-q.bid)/((q.ask+q.bid)/2) > .02:
        return None, "premium_or_spread"
    if lot_size <= 0 or q.ask_size < lot_size:
        return None, "insufficient_size"
    return {"entry":q.ask,"time":now,"lot_size":lot_size,
            "stop":q.ask*.92,"target":q.ask*1.30,
            "deadline":now+timedelta(minutes=15)}, None

def exit_check(position, now, q):
    if not valid(q,now) or q.timestamp <= position["time"] or q.bid_size < position["lot_size"]:
        return None, "unpriced_exit" if now >= position["deadline"] else "invalid_quote"
    reason = ("stop" if q.bid <= position["stop"] else
              "target" if q.bid >= position["target"] else
              "timeout" if now >= position["deadline"] else None)
    if reason is None:
        return None,None
    return {"reason":reason,"exit":q.bid,"gross_return":q.bid/position["entry"]-1,
            "gross_pnl":(q.bid-position["entry"])*position["lot_size"],
            "exit_delay_seconds":max(0,(now-position["deadline"]).total_seconds())},None
