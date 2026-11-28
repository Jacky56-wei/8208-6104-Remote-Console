# -*- coding: utf-8 -*-
"""突波／雜訊量測：過衝、欠衝、漣波 Vpp、穩態窗口、限值與判定、列序。"""
import os
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("U2_TOOL_DIR", os.environ.get("U2_TOOL_DIR") or "/workspace/Autotest_tool")

from scope_wave_saver import noise as NZ  # noqa: E402
from scope_wave_saver import sequencing as SQ  # noqa: E402
from scope_wave_saver import speccmp as SC  # noqa: E402
from scope_wave_saver.lecroy import sim_waves  # noqa: E402


def wave(v=1.2, ripple_pp=0.0, f=40e3, ring=0.0, ring_t=2e-3, n=10000, t0=-2e-3, t1=8e-3, seed=1):
    t = np.linspace(t0, t1, n)
    y = np.full(n, v) + 0.5 * ripple_pp * np.sin(2 * np.pi * f * t)
    if ring:
        x = np.clip(t - ring_t, 0, None)
        y = y + ring * v * np.exp(-x / 0.25e-3) * np.sin(2 * np.pi * 2.5e3 * x) * (t >= ring_t)
    return t, y + 1e-4 * np.random.default_rng(seed).standard_normal(n)


class MeasureTest(unittest.TestCase):
    def test_ripple_vpp_and_vss(self):
        t, y = wave(1.2, ripple_pp=0.024)                       # 24 mVpp = 2 %
        r = NZ.measure(t, y, nom=1.2)
        self.assertEqual(r["status"], "ok")
        self.assertAlmostEqual(r["vss"], 1.2, places=3)
        self.assertAlmostEqual(r["rip_pp"], 0.024, delta=0.003)
        self.assertAlmostEqual(r["rip_pct"], 2.0, delta=0.3)
        self.assertAlmostEqual(r["rms"], 0.024 / 2 / np.sqrt(2), delta=0.002)

    def test_overshoot_undershoot_after_step(self):
        t, y = wave(1.0, ripple_pp=0.004, ring=0.08)            # 先過衝約 0.08·e^-0.4 ≈ 5.4 %，再欠衝
        r = NZ.measure(t, y, nom=1.0)
        self.assertEqual(r["status"], "ok")
        self.assertGreater(r["ovs_pct"], 4.5)
        self.assertLess(r["ovs_pct"], 6.5)
        self.assertGreater(r["uns_pct"], 1.5)
        self.assertLess(r["uns_pct"], 4.0)
        self.assertGreater(r["t_ovs"], 2e-3)
        # 窗口要避開環鈴：漣波不能被環鈴帶大
        self.assertLess(r["rip_pp"], 0.01)
        self.assertGreater(r["t_a"], 2.5e-3)

    def test_median_robust_to_single_spike(self):
        t, y = wave(1.8)
        y[7000] += 0.5                                          # 單點突波：Vss 不動、算進過衝
        r = NZ.measure(t, y, nom=1.8)
        self.assertAlmostEqual(r["vss"], 1.8, places=3)
        self.assertGreater(r["ovs_v"], 0.3)

    def test_bandwidth_limit_removes_fast_noise(self):
        n = 20000
        t = np.linspace(0, 40e-6, n)
        y = 1.2 + 0.01 * np.sin(2 * np.pi * 100e6 * t)         # 100 MHz 雜訊 20 mVpp，dt = 2 ns
        raw = NZ.measure(t, y, nom=1.2, settle_s=1e-6)
        lim = NZ.measure(t, y, nom=1.2, settle_s=1e-6, bw_hz=20e6)
        self.assertGreater(raw["rip_pp"], 0.018)
        self.assertLess(lim["rip_pp"], 0.25 * raw["rip_pp"])

    def test_bw_filter_off_is_identity(self):
        y = np.arange(100.0)
        self.assertTrue(np.array_equal(NZ.bw_filter(y, 1e-6, 0.0), y))

    def test_power_down_uses_head_window(self):
        t = np.linspace(-2e-3, 8e-3, 10000)
        y = np.where(t < 3e-3, 1.2, 0.0) + 1e-4 * np.random.default_rng(2).standard_normal(len(t))
        r = NZ.measure(t, y, nom=1.2)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["where"], "前段")
        self.assertAlmostEqual(r["vss"], 1.2, places=2)
        self.assertLess(r["t_b"], 3e-3)

    def test_never_powered_is_no_steady_state(self):
        t = np.linspace(0, 1e-2, 5000)
        r = NZ.measure(t, np.zeros(5000) + 1e-3 * np.random.default_rng(3).standard_normal(5000), nom=1.2)
        self.assertNotEqual(r["status"], "ok")
        self.assertIsNone(r["vss"])

    def test_wrong_nominal_reports_reason(self):
        t, y = wave(0.9)
        r = NZ.measure(t, y, nom=2.5)
        self.assertIn("標稱", r["status"])

    def test_short_wave(self):
        self.assertEqual(NZ.measure(np.arange(10.0), np.ones(10))["status"], "波形太短")


class JudgeTest(unittest.TestCase):
    def rows(self, **cfg):
        w = {}
        for m in ("8208HD", "6104B"):
            for ch, tw in sim_waves(m, n=10000, rng=np.random.default_rng(1)).items():
                w[(m, ch)] = tw
        return NZ.analyze(w, SQ.default_rails(), cfg)

    def test_sim_demo_values(self):
        by = {x["name"]: x for x in self.rows()}
        self.assertEqual(by["DDRVPP"]["verdict"], "FAIL")       # 示範：負載暫態過衝約 7 %
        self.assertIn("過衝", by["DDRVPP"]["why"])
        self.assertEqual(by["NFIO"]["verdict"], "FAIL")         # 示範：漣波約 2.9 %
        self.assertIn("漣波", by["NFIO"]["why"])
        self.assertEqual(by["CORE"]["verdict"], "PASS")
        self.assertEqual(by["IBUS"]["verdict"], "N/A")          # 電流只記錄
        self.assertNotIn("RSTn", by)                            # 邏輯訊號不量
        self.assertNotIn("BUSPG", by)

    def test_order_follows_sort_rails(self):
        names = [x["name"] for x in self.rows()]
        self.assertEqual(names[:3], ["VIN", "VBUS", "IBUS"])
        self.assertEqual(names[3:], ["CORE", "DDRVPP", "GPIO", "DDRIO", "PHYIO", "NFIO", "NFCORE"])

    def test_default_limits_not_counted_by_default(self):
        rows = self.rows()
        by = {x["name"]: x for x in rows}
        self.assertEqual(by["DDRVPP"]["src"], NZ.SRC_DEFAULT)
        self.assertFalse(by["DDRVPP"]["counts"])
        self.assertEqual(NZ.overall(rows), "")
        rows2 = self.rows(include_default=True)
        self.assertEqual(NZ.overall(rows2), "FAIL")

    def test_user_limit_counts(self):
        rows = self.rows(limits={"ovs_pct": 10.0, "uns_pct": 5.0, "ripple_pct": 1.0, "ripple_mv": None},
                         limits_user=("ripple_pct", "ovs_pct"))
        by = {x["name"]: x for x in rows}
        self.assertEqual(by["DDRVPP"]["verdict"], "PASS")       # 過衝限放寬到 10 %
        self.assertEqual(by["CORE"]["verdict"], "FAIL")         # 漣波限收緊到 1 %（CORE 約 0.9–1.05 %）或欠衝
        self.assertTrue(by["NFIO"]["counts"])

    def test_limit_precedence_user_design_default(self):
        cfg = dict(NZ.default_cfg(), design={"CORE": {"ripple_pct": 1.5, "ovs_pct": 3.0}},
                   rail_over={"CORE": {"ripple_pct": 0.5}})
        lim, src, _ = NZ.limits_for("CORE", cfg)
        self.assertEqual(lim["ripple_pct"], 0.5)                # 使用者
        self.assertEqual(lim["ovs_pct"], 3.0)                   # 設計表
        self.assertEqual(lim["uns_pct"], 5.0)                   # 預設
        self.assertIn("使用者", src)
        self.assertIn("設計表", src)
        self.assertIn("預設", src)
        lim2, src2, _ = NZ.limits_for("GPIO", cfg)
        self.assertEqual(src2, NZ.SRC_DEFAULT)

    def test_ripple_mv_limit(self):
        res = {"status": "ok", "ovs_pct": 0.1, "uns_pct": 0.1, "rip_pct": 0.5, "rip_pp": 0.012}
        self.assertEqual(NZ.judge(res, {"ripple_mv": 10.0})[0], "FAIL")
        self.assertEqual(NZ.judge(res, {"ripple_mv": 20.0})[0], "PASS")
        self.assertEqual(NZ.judge(res, {})[0], "N/A")           # 沒限值 → 待填
        self.assertIn("待填", NZ.judge(res, {})[1])

    def test_summary_cols(self):
        rows = self.rows()
        d = NZ.summary_cols(rows)
        self.assertEqual(d["突波雜訊判定"], "FAIL")
        self.assertIn("主DDRVPP 過衝 %", d)
        names = NZ.summary_col_names(rows)
        self.assertEqual(names[0], "輔VIN 過衝 %")

    def test_csv_and_png(self):
        import tempfile
        rows = self.rows()
        w = {}
        for m in ("8208HD", "6104B"):
            for ch, tw in sim_waves(m, n=3000, rng=np.random.default_rng(1)).items():
                w[(m, ch)] = tw
        with tempfile.TemporaryDirectory() as d:
            p = NZ.save_csv(rows, os.path.join(d, "n.csv"))
            self.assertGreater(os.path.getsize(p), 200)
            q = NZ.save_png(rows, w, os.path.join(d, "n.png"))
            self.assertGreater(os.path.getsize(q), 5000)


class SpecIntegrationTest(unittest.TestCase):
    def setUp(self):
        SC.bind()
        SC.set_form("U2")

    def _rows(self, **cfg):
        w = {}
        for m in ("8208HD", "6104B"):
            for ch, tw in sim_waves(m, n=6000, rng=np.random.default_rng(1)).items():
                w[(m, ch)] = tw
        return NZ.analyze(w, SQ.default_rails(), cfg)

    def test_noise_rows_in_spec_result(self):
        base = SC.compare_measurements({"VIN": 12.0})
        res = SC.compare_measurements({"VIN": 12.0}, noise_rows=self._rows())
        self.assertEqual(len(res["noise"]), 10)
        self.assertEqual(res["counts"], base["counts"])         # 預設限值不計總判定
        self.assertTrue(any(r["verdict"] == "FAIL" for r in res["noise"]))
        self.assertIn("不計總判定", [r for r in res["noise"] if r["verdict"] == "FAIL"][0]["why"])
        res2 = SC.compare_measurements({"VIN": 12.0}, noise_rows=self._rows(include_default=True))
        self.assertGreater(res2["counts"]["FAIL"], base["counts"]["FAIL"])
        self.assertEqual(res2["overall"], "FAIL")

    def test_design_json_optional_fields(self):
        import json
        import tempfile
        from unittest import mock
        self.assertEqual(NZ.design_limits("U2_12V_PowerIC_DesignSpec"), {})      # 隨附設計表目前沒有這些欄位
        data = {"note": "t", "rails": [{"ch": "CORE", "nom": 0.84, "min": 0.8443, "max": 0.8703, "unit": "V",
                                        "ovs_pct": 3, "ripple_mv": 12}, {"ch": "GPIO", "nom": 1.8}]}
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump(data, f)
            with mock.patch.object(SC.PP, "_path", create=True, return_value=p):
                lim = NZ.design_limits("x")
        self.assertEqual(lim, {"CORE": {"ovs_pct": 3.0, "ripple_mv": 12.0}})
        cfg = dict(NZ.default_cfg(), design=lim)
        l, src, _ = NZ.limits_for("CORE", cfg)
        self.assertEqual((l["ovs_pct"], l["ripple_mv"], l["uns_pct"]), (3.0, 12.0, 5.0))
        self.assertIn("設計表", src)

    def test_csv_cols_mentions_noise(self):
        res = SC.compare_measurements({"VIN": 12.0}, noise_rows=self._rows())
        self.assertIn("突波雜訊", SC.csv_cols(res)["規格明細"])


if __name__ == "__main__":
    unittest.main()
