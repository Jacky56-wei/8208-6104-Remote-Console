# -*- coding: utf-8 -*-
"""⑨ 規格判比：跟隨 Autotest_tool powerprofile／designspec 語意。"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("U2_TOOL_DIR", os.environ.get("U2_TOOL_DIR") or "/workspace/Autotest_tool")

from scope_wave_saver import speccmp as SC  # noqa: E402


class SpecCmpTest(unittest.TestCase):
    def setUp(self):
        SC.bind()
        SC.set_form("U2")

    def test_names_from_bundled(self):
        names = SC.PP.names()
        self.assertIn("U2_12V_PowerIC_DesignSpec", names)
        self.assertIn("M2_3V3_PowerIC_DesignSpec", names)

    def test_match_vin(self):
        self.assertEqual(SC.PP.match_vin(12.0), "U2_12V_PowerIC_DesignSpec")
        self.assertEqual(SC.PP.match_vin(3.3), "M2_3V3_PowerIC_DesignSpec")

    def test_u2_compare_wording(self):
        self.assertEqual(SC.voltage_compare("CORE", 0.8573), "設計範圍內")
        self.assertIn("低於設計 min", SC.voltage_compare("CORE", 0.84))
        self.assertIn("高於設計 max", SC.voltage_compare("CORE", 0.9))
        self.assertIsNone(SC.voltage_compare("PHYIO", 1.2))  # 待填，無 min–max

    def test_map_pass_fail(self):
        self.assertEqual(SC._map_verdict("設計範圍內", 0.85, True), ("PASS", "設計範圍內"))
        self.assertEqual(SC._map_verdict("低於設計 min 0.8443 V", 0.84, True)[0], "FAIL")
        self.assertEqual(SC._map_verdict(None, 1.2, False)[0], "N/A")

    def test_m2_switch(self):
        SC.set_form("M2")
        self.assertEqual(SC.PP.get(), "M2_3V3_PowerIC_DesignSpec")
        self.assertEqual(SC.DUT.vin(), 3.3)
        self.assertEqual(SC.voltage_compare("CORE", 0.78), "設計範圍內")
        self.assertEqual(SC.vnom("CORE"), 0.78)
        self.assertEqual(SC.vnom("DDRVPP"), 1.8)
        self.assertEqual(SC.vnom("VIN"), 3.3)

    def test_public_and_csv(self):
        SC.set_form("U2")
        r = SC.compare_measurements(SC.demo_vnom_map())
        self.assertEqual(r["overall"], "PASS")
        pub = next(x for x in r["rows"] if x["kind"] == "public")
        self.assertEqual(pub["verdict"], "PASS")
        cols = SC.csv_cols(r)
        self.assertIn("U.2", cols["規格"])
        self.assertEqual(cols["規格總判定"], "PASS")
        self.assertIn("CORE PASS", cols["規格明細"])


if __name__ == "__main__":
    unittest.main()


class LayoutMigrateTest(unittest.TestCase):
    def test_v1_to_v2(self):
        import json, tempfile, os
        from scope_wave_saver.foldui import LayoutStore, LAYOUT_VERSION
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "collapsed": {"spec": False},
                           "order": {"left": ["trig", "spec", "arb"]},
                           "sash": {}, "weights": {}}, f)
            st = LayoutStore(path)
            self.assertEqual(st.data["version"], LAYOUT_VERSION)
            # 不重寫使用者拖過的順序；spec 仍是判比表卡片
            self.assertEqual(st.order("left", ["trig", "ch", "arb", "spec"]), ["trig", "spec", "arb", "ch"])
            with open(path, encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(saved["version"], LAYOUT_VERSION)
        finally:
            os.remove(path)
