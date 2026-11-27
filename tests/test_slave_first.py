# -*- coding: utf-8 -*-
"""輔機訊號列在前（VIN, VBUS, IBUS, BUSPG → CORE … RSTn）；「順序」仍依發生時間。"""
from __future__ import annotations

import unittest

import numpy as np

from scope_wave_saver import rails as R
from scope_wave_saver import sequencing as SQ
from scope_wave_saver import speccmp as SC


WANT = ["VIN", "VBUS", "IBUS", "BUSPG", "CORE", "DDRVPP", "GPIO", "DDRIO", "PHYIO", "NFIO", "NFCORE", "RSTn"]


def _wave(t0, t1, v0, v1, n=2000, tmin=-1e-3, tmax=5e-3):
    t = np.linspace(tmin, tmax, n)
    y = np.where(t < t0, v0, np.where(t > t1, v1, v0 + (v1 - v0) * (t - t0) / (t1 - t0)))
    return t, y


def _waves_up():
    """主機 CORE 比輔機 VBUS 更早到（用來證明列序不是時間序）。"""
    # name: (t_lo, t_hi, nom)
    times = {
        ("6104B", 1, "VIN"):     (0.10e-3, 0.20e-3, 12.0),
        ("6104B", 2, "VBUS"):    (0.80e-3, 0.90e-3, 12.0),
        ("6104B", 3, "BUSPG"):   (0.30e-3, 0.35e-3, 3.3),
        ("8208HD", 1, "CORE"):   (0.40e-3, 0.55e-3, 0.84),
        ("8208HD", 2, "DDRVPP"): (1.00e-3, 1.20e-3, 2.5),
        ("8208HD", 8, "RSTn"):   (2.00e-3, 2.05e-3, 1.8),
    }
    waves = {}
    for (m, ch, nm), (lo, hi, nom) in times.items():
        waves[(m, ch)] = _wave(lo, hi, 0.0, nom)
    # IBUS 電流：峰值在中間，無 t_on
    t = np.linspace(-1e-3, 5e-3, 2000)
    y = 0.05 + 0.2 * np.exp(-((t - 0.5e-3) / 0.2e-3) ** 2)
    waves[("6104B", 4)] = (t, y)
    return waves


class SignalOrderTest(unittest.TestCase):
    def test_signal_order_constant(self):
        self.assertEqual(R.all_names(), WANT)
        self.assertEqual(R.MODELS, (R.SLAVE_DEF, R.MASTER_DEF))
        self.assertEqual([x["name"] for x in SQ.default_rails()], WANT)
        # 6104B 硬體通道不變
        self.assertEqual(R.NAMES[R.SLAVE_DEF], ["VIN", "VBUS", "BUSPG", "IBUS"])
        by = {x["name"]: x["ch"] for x in SQ.default_rails()}
        self.assertEqual(by["IBUS"], 4)
        self.assertEqual(by["BUSPG"], 3)

    def test_analyze_lists_slave_first_but_numbers_by_time(self):
        rows = SQ.analyze(_waves_up(), SQ.default_rails(), ref="VIN", basis="hi")
        self.assertEqual([x["name"] for x in rows], WANT)
        # t_hi order: VIN, BUSPG, CORE, VBUS, DDRVPP, RSTn（IBUS 無 t_on）
        by_name = {x["name"]: x for x in rows}
        self.assertEqual(by_name["VIN"]["order"], 1)
        self.assertEqual(by_name["BUSPG"]["order"], 2)
        self.assertEqual(by_name["CORE"]["order"], 3)
        self.assertEqual(by_name["VBUS"]["order"], 4)
        self.assertIsNone(by_name["IBUS"]["order"])
        self.assertAlmostEqual(by_name["VIN"]["delay"], 0.0)
        self.assertEqual(SQ.sequence_text(rows), "VIN → BUSPG → CORE → VBUS → DDRVPP → RSTn")
        table = SQ.table(rows)
        self.assertEqual([r[4] for r in table], WANT)  # rail column

    def test_spec_compare_ibus_with_slave(self):
        SC.set_form("U2")
        SC.set_profile("U2_12V_PowerIC_DesignSpec")
        meas = {n: R.VNOM[n] for n in WANT if n != "IBUS"}
        meas["IBUS"] = 0.1
        # CORE 設計範圍約 0.853–0.884，標稱 0.84 會 FAIL；給一個在範圍內的值
        meas["CORE"] = 0.86
        result = SC.compare_measurements(meas)
        chs = [r["ch"] for r in result["rows"] if r.get("kind") == "design"]
        # 公開輸入 VIN 列在設計列之後，不算 design；IBUS 在 VBUS 之後、BUSPG 之前
        self.assertEqual(chs[:4], ["VIN", "VBUS", "IBUS", "BUSPG"])
        self.assertEqual(chs[4], "CORE")
        self.assertLess(chs.index("IBUS"), chs.index("BUSPG"))
        self.assertEqual(chs[-1], "RSTn")


if __name__ == "__main__":
    unittest.main()
