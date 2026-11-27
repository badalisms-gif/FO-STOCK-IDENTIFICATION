import unittest,tempfile
from pathlib import Path
from research_v3.forward_signal_ledger import Ledger
class LedgerTest(unittest.TestCase):
    def test_retention(self):
        with tempfile.TemporaryDirectory() as d:
            l=Ledger(str(Path(d)/"ledger.db"))
            row={"session_date":"2026-11-30","signal_id":"a","instrument_key":"NSE_FO|1","entry":25,"stop_loss":23,"target":32.5,"trade_status":"ACTIVE"}
            l.register(row)
            l.update(row["session_date"],"a",{"filter_status":"FILTER LOST","current_score":.55})
            l.register(dict(row,entry=99))
            self.assertEqual(l.rows(row["session_date"])[0]["entry"],25)
            l.update(row["session_date"],"a",{"trade_status":"TARGET HIT","exit_price":33})
            l.update(row["session_date"],"a",{"trade_status":"ACTIVE","exit_price":1})
            l.close()
            l=Ledger(str(Path(d)/"ledger.db"))
            self.assertEqual(l.rows(row["session_date"])[0]["trade_status"],"TARGET HIT")
            self.assertEqual(l.rows(row["session_date"])[0]["exit_price"],33)
            l.close()
