# -*- coding: utf-8 -*-
"""上電後自動 rescan 認盤：判定邏輯、link 解析、重試／輪詢、背景結果、掉盤停止。"""
from __future__ import annotations

import random
import threading
import unittest

from scope_wave_saver import nvmecheck as NV
from scope_wave_saver import nvmehost as NH


def drv(sn="SN1", link="16GT/s x4", dev="/dev/nvme0n1", cw=None):
    d = {"dev": dev, "model": "M", "serial": sn, "fw": "1", "bdf": "0000:01:00.0", "link": link}
    if cw is not None:
        d["smart_cw"] = cw
    return d


class ParseLink(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(NV.parse_link("16GT/s x4"), (16.0, 4))
        self.assertEqual(NV.parse_link("16.0 GT/s PCIe x4"), (16.0, 4))
        self.assertEqual(NV.parse_link("Gen5 x2"), (32.0, 2))
        self.assertEqual(NV.parse_link("Speed 8GT/s, Width x4"), (8.0, 4))
        self.assertEqual(NV.parse_link(""), (None, None))
        self.assertEqual(NV.link_text(16.0, 4), "Gen4 x4")


class Judge(unittest.TestCase):
    def test_ok_sets_baseline(self):
        b = NV.Baseline()
        r = NV.judge([drv()], b)
        self.assertEqual(r["result"], NV.OK)
        self.assertTrue(b.ready)
        self.assertEqual(r["baseline"], "Gen4 x4")

    def test_not_found_is_drop(self):
        r = NV.judge([], NV.Baseline())
        self.assertEqual(r["result"], NV.DROP)
        self.assertFalse(r["ok"])
        self.assertEqual(r["found"], 0)

    def test_drop_does_not_set_baseline(self):
        b = NV.Baseline()
        NV.judge([], b)
        self.assertFalse(b.ready)

    def test_fewer_than_baseline_is_drop(self):
        b = NV.Baseline()
        NV.judge([drv("A"), drv("B", dev="/dev/nvme1n1")], b)
        r = NV.judge([drv("A")], b)
        self.assertEqual(r["result"], NV.DROP)
        self.assertEqual(r["missing"], ["B"])

    def test_downgrade_vs_baseline(self):
        b = NV.Baseline()
        NV.judge([drv()], b)
        self.assertEqual(NV.judge([drv(link="8GT/s x4")], b)["result"], NV.DOWN_SPEED)
        self.assertEqual(NV.judge([drv(link="16GT/s x2")], b)["result"], NV.DOWN_WIDTH)
        self.assertEqual(NV.judge([drv(link="8GT/s x2")], b)["result"], NV.DOWN_BOTH)
        self.assertEqual(NV.judge([drv(link="16GT/s x4")], b)["result"], NV.OK)

    def test_downgrade_does_not_update_baseline(self):
        b = NV.Baseline()
        r = NV.judge([drv(link="8GT/s x4")], b)             # 第一次就是 Gen3 → 當基準
        self.assertEqual(r["result"], NV.OK)
        self.assertEqual(NV.judge([drv(link="16GT/s x4")], b)["result"], NV.OK)   # 變快不算異常

    def test_expected_overrides_baseline(self):
        b = NV.Baseline()
        r = NV.judge([drv(link="8GT/s x4")], b, expected="Gen4 x4")
        self.assertEqual(r["result"], NV.DOWN_SPEED)
        self.assertFalse(b.ready)
        self.assertEqual(NV.judge([drv(link="16GT/s x4")], b, expected="Gen4 x4")["result"], NV.OK)
        self.assertEqual(NV.judge([drv(link="16GT/s x2")], NV.Baseline(), expected="x4")["result"], NV.DOWN_WIDTH)

    def test_unknown_link_not_flagged(self):
        b = NV.Baseline()
        NV.judge([drv()], b)
        self.assertEqual(NV.judge([drv(link="")], b)["result"], NV.OK)

    def test_smart_warning(self):
        r = NV.judge([drv(cw=4)], NV.Baseline())
        self.assertEqual(r["result"], NV.SMART_WARN)
        self.assertEqual(NV.judge([drv(cw=0)], NV.Baseline())["result"], NV.OK)
        self.assertEqual(NV.judge([drv(cw=None)], NV.Baseline())["result"], NV.OK)

    def test_priority_drop_over_others(self):
        b = NV.Baseline()
        NV.judge([drv("A"), drv("B", dev="/dev/nvme1n1")], b)
        r = NV.judge([drv("A", link="8GT/s x1", cw=1)], b)
        self.assertEqual(r["result"], NV.DROP)


class FakeHost(NH.SimHost):
    """可控的假主機：依序回傳每次列舉的結果。"""

    def __init__(self, seq):
        super().__init__("ok")
        self.seq, self.rescans = list(seq), 0

    def run(self, cmd, timeout=30):
        if "pci/rescan" in cmd:
            self.rescans += 1
        return 0, "", ""

    def sim_drives(self):
        return list(self.seq.pop(0) if len(self.seq) > 1 else self.seq[0])


class RunCheck(unittest.TestCase):
    CFG = {"settle_s": 0, "timeout_s": 2, "retries": 2, "poll_s": 1.0}

    def _clock(self):
        t = [0.0]
        return (lambda: t[0]), (lambda s: t.__setitem__(0, t[0] + s))

    def test_late_drive_found_by_polling(self):
        clock, sleep = self._clock()
        h = FakeHost([[], [], [drv()]])
        r = NV.run_check(h, self.CFG, NV.Baseline(), sleep=sleep, clock=clock)
        self.assertEqual(r["result"], NV.OK)
        self.assertEqual(r["attempts"], 1)

    def test_drop_retries_then_gives_up(self):
        clock, sleep = self._clock()
        h = FakeHost([[]])
        r = NV.run_check(h, self.CFG, NV.Baseline(), sleep=sleep, clock=clock)
        self.assertEqual(r["result"], NV.DROP)
        self.assertEqual(r["attempts"], 3)
        self.assertEqual(h.rescans, 3)

    def test_retry_recovers(self):
        clock, sleep = self._clock()
        h = FakeHost([[]] * 3 + [[drv()]])   # 第 1 次 rescan 的 3 次輪詢都沒有，第 2 次 rescan 才出現
        r = NV.run_check(h, dict(self.CFG, timeout_s=2), NV.Baseline(), sleep=sleep, clock=clock)
        self.assertEqual(r["result"], NV.OK)
        self.assertEqual(r["attempts"], 2)

    def test_downgrade_not_retried(self):
        clock, sleep = self._clock()
        b = NV.Baseline()
        NV.judge([drv()], b)
        h = FakeHost([[drv(link="8GT/s x4")]])
        r = NV.run_check(h, self.CFG, b, sleep=sleep, clock=clock)
        self.assertEqual(r["result"], NV.DOWN_SPEED)
        self.assertEqual(r["attempts"], 1)

    def test_ssh_failure_is_error_not_drop(self):
        class Bad(NH.SimHost):
            def run(self, cmd, timeout=30):
                raise NH.NvmeError("SSH 斷線")
        clock, sleep = self._clock()
        r = NV.run_check(Bad(), self.CFG, NV.Baseline(), sleep=sleep, clock=clock)
        self.assertEqual(r["result"], NV.ERR)
        self.assertIn("SSH", r["detail"])

    def test_stop_event_aborts(self):
        clock, sleep = self._clock()
        ev = threading.Event()
        ev.set()
        self.assertIsNone(NV.run_check(FakeHost([[drv()]]), self.CFG, NV.Baseline(), sleep=sleep, clock=clock, stop_evt=ev))


class Smart(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(NH.parse_smart_cw('{"critical_warning": 4, "temperature": 310}'), 4)
        self.assertEqual(NH.parse_smart_cw("critical_warning                    : 0x4\ntemperature : 40 C"), 4)
        self.assertEqual(NH.parse_smart_cw("critical_warning : 0"), 0)
        self.assertIsNone(NH.parse_smart_cw("command not found"))


class SimScenario(unittest.TestCase):
    def test_rate_zero_is_ok(self):
        rng = random.Random(1)
        self.assertTrue(all(NV.pick_sim_scenario(0, rng) == "ok" for _ in range(50)))

    def test_rate_one_never_ok(self):
        rng = random.Random(1)
        got = {NV.pick_sim_scenario(1, rng) for _ in range(200)}
        self.assertNotIn("ok", got)
        self.assertTrue(got <= set(NV.SIM_SCENARIOS))
        self.assertIn("drop", got)

    def test_mostly_ok(self):
        rng = random.Random(7)
        n_ok = sum(NV.pick_sim_scenario(0.2, rng) == "ok" for _ in range(1000))
        self.assertTrue(700 < n_ok < 900)


class AutoRescan(unittest.TestCase):
    def _cfg(self, **k):
        c = {"on": True, "sim": True, "host": "1.2.3.4", "auto": True, "settle_s": 0, "timeout_s": 1, "retries": 1}
        c.update(k)
        return c

    def test_disabled_by_default(self):
        self.assertFalse(NV.AutoRescan({"on": True}).enabled)
        self.assertFalse(NV.AutoRescan({"auto": True}).enabled)
        a = NV.AutoRescan({"on": True})
        self.assertFalse(a.kick(1, "x"))

    def test_sequence_and_baseline(self):
        import tempfile
        d = tempfile.mkdtemp()
        a = NV.AutoRescan(self._cfg(sim_seq="ok,down_speed,drop,late,smart"), run_dir_fn=lambda: d, log=lambda *_: None)
        got = []
        for i in range(1, 6):
            self.assertTrue(a.kick(i, "測試", 0.0))
            got.append(a.wait(i)["result"])
        self.assertEqual(got, [NV.OK, NV.DOWN_SPEED, NV.DROP, NV.OK, NV.SMART_WARN])
        import os
        with open(os.path.join(d, "nvme_check.log"), encoding="utf-8") as f:
            txt = f.read()
        self.assertEqual(txt.count("\t#"), 5)
        self.assertIn("掉盤", txt)

    def test_csv_cols(self):
        r = NV.judge([drv()], NV.Baseline())
        r["phase"], r["attempts"] = "ARB Ramp 上電後", 1
        c = NV.csv_cols(r)
        self.assertEqual(c["NVMe 判定"], "OK")
        self.assertEqual(c["NVMe 速度"], "Gen4")
        self.assertEqual(c["NVMe 寬度"], "x4")
        self.assertEqual(set(NV.csv_cols(None)), set(NV.CSV_COLS))


class LineText(unittest.TestCase):
    def test_lines(self):
        b = NV.Baseline()
        ok = NV.judge([drv()], b)
        ok.update(seq=3, phase="ARB Ramp 上電後", attempts=1)
        text, _tip, lv = NH.homepage_line(None, None, ok)
        self.assertIn("認盤 OK", text)
        self.assertEqual(lv, "PASS")
        drop = NV.judge([], b)
        drop.update(seq=4, phase="Brownout 恢復後", attempts=3)
        text, _tip, lv = NH.homepage_line(None, None, drop)
        self.assertIn("掉盤", text)
        self.assertIn("rescan 3 次", text)
        self.assertEqual(lv, "FAIL")
        slow = NV.judge([drv(link="8GT/s x4")], b)
        slow.update(seq=5, phase="x", attempts=1)
        text, _tip, lv = NH.homepage_line(None, None, slow)
        self.assertIn("降速", text)
        self.assertEqual(lv, "WARN")


if __name__ == "__main__":
    unittest.main()


class RealHostCommands(unittest.TestCase):
    """非示範主機：只驗證送出的指令（不連任何機器）。"""

    class Rec:
        def __init__(self, replies=None):
            self.cmds, self.replies = [], replies or {}

        def run(self, cmd, timeout=30):
            self.cmds.append(cmd)
            for k, v in self.replies.items():
                if k in cmd:
                    return 0, v, ""
            return 0, "", ""

    def test_remove_then_rescan(self):
        h = self.Rec()
        NH.rescan(h, wait_s=0, remove_bdfs=["0000:01:00.0", "evil; rm -rf /"])
        self.assertEqual(len(h.cmds), 2)
        self.assertIn("/sys/bus/pci/devices/0000:01:00.0/remove", h.cmds[0])
        self.assertIn("pci/rescan", h.cmds[1])

    def test_rescan_only_by_default(self):
        h = self.Rec()
        NH.rescan(h, wait_s=0)
        self.assertEqual(len(h.cmds), 1)

    def test_attach_smart(self):
        h = self.Rec({"smart-log /dev/nvme0": '{"critical_warning": 0}', "smart-log /dev/nvme1": '{"critical_warning": 8}'})
        rows = [drv("A", dev="/dev/nvme0n1"), drv("B", dev="/dev/nvme1n1"), drv("C", dev="/dev/weird")]
        NH.attach_smart(h, rows)
        self.assertEqual([r["smart_cw"] for r in rows], [0, 8, None])
        self.assertEqual(NV.judge(rows[:2], NV.Baseline())["result"], NV.SMART_WARN)
