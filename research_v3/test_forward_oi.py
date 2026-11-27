import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from research_v3.forward_oi_buffer import OIBuffer
class OITest(unittest.TestCase):
    def test_minute_alignment(self):
        b=OIBuffer()
        t=datetime(2026,11,30,10,0,58,tzinfo=ZoneInfo("Asia/Kolkata"))
        self.assertTrue(b.ingest("future",1000,int(t.timestamp()*1000),t))
        self.assertEqual(b.attach("2026-11-30","future",600,[1,2,1,2,10]),[1,2,1,2,10,1000])
        self.assertIsNone(b.attach("2026-11-30","future",601,[1,2,1,2,10]))
        self.assertFalse(b.ingest("future",1000,None,t))
