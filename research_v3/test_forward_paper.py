import unittest
from datetime import datetime,timedelta,timezone
from research_v3.forward_paper_execution import Quote,enter,exit_check
class TestPaper(unittest.TestCase):
    def test_entry_exit(self):
        t=datetime(2026,11,30,10,tzinfo=timezone(timedelta(hours=5,minutes=30)))
        q=Quote(t,99,100,100,100)
        p,e=enter(t,t,q,t.date()+timedelta(days=1),50)
        self.assertIsNone(e)
        later=t+timedelta(seconds=10)
        result,e=exit_check(p,later,Quote(later,90,91,100,100))
        self.assertEqual(result["reason"],"stop")
        self.assertAlmostEqual(result["gross_return"],-.1)
    def test_stale(self):
        t=datetime(2026,11,30,10,tzinfo=timezone.utc)
        p,e=enter(t,t,Quote(t-timedelta(seconds=6),99,100,100,100),t.date()+timedelta(days=1),50)
        self.assertIsNone(p)
        self.assertEqual(e,"invalid_quote")
if __name__=="__main__":unittest.main()
