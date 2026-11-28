# -*- coding: utf-8 -*-
"""重產操作手冊截圖（示範模式、xvfb 1920×1080）。

用法（Linux，有 xvfb 與 U2_TOOL_DIR）：
    U2_TOOL_DIR=/path/to/Autotest_tool python3 docs/make_images.py [raw|crop|all] [名稱…]
  raw  ：用 --shot 抓整個畫面到 docs/_raw/（不進版控）
  crop ：從 _raw 裁切／標註，輸出 docs/img/
"""
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RAW = os.path.join(HERE, "_raw")

LEFT = "conn,trig,ch,pm,save,cap,seq,arb,spec,noise"
RIGHT = "shots,waves,seqv,specv,noisev,pmt,saved,log,nvme"


def fold(open_left="", open_right=""):
    """回傳 --fold / --unfold 參數：除了指定的，其餘全部收合。"""
    ol = set(x for x in open_left.split(",") if x)
    orr = set(x for x in open_right.split(",") if x)
    f = [k for k in LEFT.split(",") if k not in ol] + [k for k in RIGHT.split(",") if k not in orr]
    return ["--fold", ",".join(f), "--unfold", ",".join(sorted(ol | orr))]


# name: (額外參數, 幾秒後截圖)
SCEN = {
    "ov": (fold("conn,trig", "shots"), 4),
    "conn": (fold("conn"), 4),
    "dut_u2": (fold("conn"), 4),
    "dut_m2": (["--form", "M2"] + fold("conn"), 4),
    "trig": (fold("trig"), 4),
    "chpm": (fold("ch,pm", "shots"), 4),
    "savecap": (fold("save,cap", "shots"), 4),
    "seqcfg": (fold("seq", "seqv"), 4),
    "run_stack": (["--capture", "1", "--arb-tpl", "RAMP"] + fold("trig", "shots,saved"), 8),
    "run_merge": (["--capture", "1", "--view", "merge"] + fold("cap", "waves,saved"), 8),
    "run_seq_up": (["--capture", "1", "--view", "seq", "--sim-window=-8,10"] + fold("seq", "seqv,saved"), 8),
    "run_seq_down": (["--capture", "2", "--view", "seq", "--sim-window=-3,10"] + fold("seq", "seqv,saved"), 11),
    "pm": (["--capture", "1"] + fold("pm", "pmt"), 8),
    "arb_ramp": (["--scroll", "0.3", "--arb", "1", "--arb-tpl", "RAMP"] + fold("arb", "seqv"), 14),
    "arb_brown": (["--scroll", "0.3", "--arb", "1", "--arb-tpl", "BROWNOUT"] + fold("arb", "seqv"), 20),
    "arb_onoff": (["--scroll", "0.3", "--arb", "1", "--arb-tpl", "ONOFFON"] + fold("arb", "seqv"), 16),
    "arb_idle": (["--arb-tpl", "BROWNOUT"] + fold("arb", "shots"), 4),
    "noise": (["--capture", "1"] + fold("noise", "noisev"), 8),
    "spec_u2": (["--capture", "1", "--form", "U2"] + fold("spec", "specv"), 8),
    "spec_m2": (["--capture", "1", "--form", "M2"] + fold("spec", "specv"), 8),
    "nvme_manual": (["--nvme", "--scroll", "1.0"] + fold("arb,conn", "nvme"), 6),
    "nvme_auto": (["--nvme-auto", "--capture", "1", "--scroll", "1.0"] + fold("arb,conn", "nvme,saved"), 10),
    "nvme_drop": (["--nvme-auto", "--nvme-stop-on-drop", "--nvme-sim-seq", "ok,drop", "--capture", "0"]
                  + fold("arb,conn", "shots,saved"), 14),
    "cont": (["--capture", "3"] + fold("cap", "seqv,saved,log"), 13),
    "unplug": (["--capture", "0", "--unplug", "6104B:6@3"] + fold("conn,cap", "shots,log"), 7),
    "folded": (fold("", ""), 4),
}


def run_raw(name, outdir):
    args, wait = SCEN[name]
    out = os.path.join(RAW, name + ".png")
    folder = os.path.join("/tmp", "wss_" + name)
    subprocess.run(["rm", "-rf", folder])
    os.makedirs(folder, exist_ok=True)
    if "--capture" not in args and "--arb" not in args:
        folder = r"D:\scope waveform"  # 只顯示用的路徑（沒有擷取就不會寫入）
    cmd = ["xvfb-run", "-a", "-s", "-screen 0 1920x1080x24", sys.executable, os.path.join(HERE, "_geom.py"),
           os.path.join(RAW, name + ".json"),
           "--folder", folder, "--shot", out, "--shot-after", str(wait)] + args
    env = dict(os.environ, PYTHONPATH=ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""))
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, env=env)
    print(name, "ok" if os.path.exists(out) else "FAIL", r.stderr[-200:].strip())


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "crop":
        for fn in OUTS:
            if len(sys.argv) == 2 or fn.__name__[4:] in sys.argv[2:]:
                fn()
        return
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    names = sys.argv[2:] or list(SCEN)
    os.makedirs(RAW, exist_ok=True)
    if mode in ("raw", "all"):
        with ThreadPoolExecutor(4) as ex:
            list(ex.map(lambda n: run_raw(n, RAW), names))



# =============================================================== 裁切與標註
from PIL import Image, ImageDraw, ImageFont  # noqa: E402
import json  # noqa: E402

IMG = os.path.join(HERE, "img")
RED = (220, 38, 38)
FONT_B = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
FONT_R = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"


def _font(size, bold=True):
    for idx in (2, 0):   # TC / 預設
        try:
            return ImageFont.truetype(FONT_B if bold else FONT_R, size, index=idx)
        except Exception:  # noqa: BLE001
            continue
    return ImageFont.load_default()


class Shot:
    """一張原始截圖＋它的元件座標；可標註、裁切、存檔。"""

    def __init__(self, name):
        self.name = name
        self.im = Image.open(os.path.join(RAW, name + ".png")).convert("RGB")
        self.g = json.load(open(os.path.join(RAW, name + ".json"), encoding="utf-8"))
        self.d = ImageDraw.Draw(self.im)

    # ---- 找元件：文字包含 text 的第 nth 個（可限 x/y 範圍）
    def find(self, text, nth=0, cls=None, xmin=0, xmax=9999, ymin=0, ymax=9999):
        hits = [e for e in self.g if text in e["text"] and e["h"] > 8 and (cls is None or e["cls"].endswith(cls))
                and xmin <= e["x"] < xmax and ymin <= e["y"] < ymax]
        if len(hits) <= nth:
            raise KeyError(f"{self.name}: 找不到「{text}」#{nth}")
        e = hits[nth]
        return (e["x"], e["y"], e["x"] + e["w"], e["y"] + e["h"])

    def rect(self, b, pad=3, w=4):
        x0, y0, x1, y1 = b
        self.d.rounded_rectangle((x0 - pad, y0 - pad, x1 + pad, y1 + pad), radius=6, outline=RED, width=w)

    def num(self, x, y, n, r=15):
        self.d.ellipse((x - r, y - r, x + r, y + r), fill=RED, outline="white", width=2)
        f = _font(int(r * 1.25))
        self.d.text((x, y - 1), str(n), fill="white", font=f, anchor="mm")

    def mark(self, b, n, side="tl", pad=3):
        """框起來並在角落放編號。side: tl 左上、tr 右上、l 左外、r 右外。"""
        self.rect(b, pad)
        x0, y0, x1, y1 = b
        pos = {"tl": (x0 - pad, y0 - pad), "tr": (x1 + pad, y0 - pad),
               "l": (x0 - 24, (y0 + y1) // 2), "r": (x1 + 24, (y0 + y1) // 2)}[side]
        self.num(pos[0], pos[1], n)

    def text(self, x, y, s, size=20, fill=RED):
        f = _font(size)
        bb = self.d.textbbox((x, y), s, font=f)
        self.d.rectangle((bb[0] - 4, bb[1] - 2, bb[2] + 4, bb[3] + 2), fill=(255, 255, 255))
        self.d.text((x, y), s, fill=fill, font=f)

    def save(self, out, box=None, scale=1.0):
        im = self.im.crop(box) if box else self.im
        if scale != 1.0:
            im = im.resize((int(im.width * scale), int(im.height * scale)), Image.LANCZOS)
        os.makedirs(IMG, exist_ok=True)
        path = os.path.join(IMG, out)
        # 量化成 256 色可大幅縮小（UI 截圖色彩少）
        q = im.quantize(256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        q.save(path, optimize=True)
        print("  ->", out, im.size, os.path.getsize(path) // 1024, "KB")
        return im


def hdr(s, key):
    """卡片標題列（含收合箭頭）的範圍：key 例如 '③'。"""
    x0, y0, x1, y1 = s.find(key, cls="Label")
    return (19, y0 - 3, x1 + 30, y1 + 3)


def card_circle(s, key, n, x):
    x0, y0, x1, y1 = s.find(key, cls="Label")
    s.num(x, (y0 + y1) // 2, n)


def out_overview():
    s = Shot("ov")
    cards = ["②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩"]
    for i, k in enumerate(cards):
        card_circle(s, k, i + 2, 1040)
    # ① 連線列
    x0, y0, x1, y1 = s.find("① 儀器連線")
    s.rect((18, y0 - 6, 1112, y1 + 42), pad=2)
    s.num(1135 - 20, y0 + 12, 1)
    # 工具列、DUT、狀態
    s.rect(s.find("▶ 擷取＋存檔")[:2] + s.find("匯出 CSV")[2:], pad=3)
    s.num(1380, 28, "A")
    s.rect((12, 80, 700, 160), pad=2)
    s.num(740, 118, "B")
    s.rect((1735, 8, 1912, 48), pad=3)
    s.num(1715, 28, "C")
    # 右欄
    for i, k in enumerate(["兩台實機畫面", "合一張", "上 / 下電時序", "設計規格判比（實測 vs 電源設計表）", "突波／雜訊量測結果", "P 量測結果", "存檔結果", "紀錄"]):
        x0, y0, x1, y1 = s.find(k, cls="Label", xmin=1100)
        s.num(1860, (y0 + y1) // 2, f"R{i + 1}", r=17)
    return s.save("01_overview.png", (0, 0, 1920, 1040))


def out_toolbar():
    s = Shot("ov")
    items = [("▶ 擷取＋存檔", 1), ("■ 停止", 2), ("⚡ ARB＋擷取", 3), ("⟳ 實圖即時更新", 4), ("📷 存上下實圖", 5),
             ("PY_VAR", 6), ("⤓ 讀回儀器設定", 7), ("⤒ 寫入儀器設定", 8), ("結果資料夾", 9), ("匯出 CSV", 10), ("已同步", 11)]
    tb = s.im.crop((0, 0, 1920, 52))
    cv = Image.new("RGB", (1920, 90), "white")
    cv.paste(tb, (0, 0))
    d = ImageDraw.Draw(cv)
    f = _font(19)
    for t, n in items:
        b = s.find(t) if t != "PY_VAR" else s.find("PY_VAR", cls="Combobox")
        d.rounded_rectangle((b[0] - 2, b[1] - 2, b[2] + 2, b[3] + 2), radius=5, outline=RED, width=3)
        cx = (b[0] + b[2]) // 2
        d.line((cx, b[3] + 2, cx, 62), fill=RED, width=2)
        d.ellipse((cx - 14, 62, cx + 14, 90), fill=RED)
        d.text((cx, 76), str(n), fill="white", font=f, anchor="mm")
    s.im = cv
    return s.save("09_toolbar.png")


def _top(s):
    return (0, 0, 1920, 1040)


def out_dut_conn():
    """DUT 規格列（U.2 / M.2 兩種選法）＋ ① 連線列。"""
    a, b = Shot("dut_u2"), Shot("dut_m2")
    parts = []
    for sh, cap in ((a, "選 U.2／U.3 → 標稱 VIN 12 V"), (b, "選 M.2 → 標稱 VIN 3.3 V")):
        sh.mark(sh.find("U.2／U.3（VIN 12 V）"), 1, "tl") if sh is a else sh.mark(sh.find("M.2（VIN 3.3 V）"), 1, "tl")
        sh.mark(sh.find("PY_VAR", cls="Combobox", ymin=100, ymax=160), 2, "tr")
        sh.mark(sh.find("PASS", cls="Label", ymin=70, ymax=120), 3, "tr")
        sh.text(540, 88, cap, 20)
        parts.append(sh.im.crop((0, 70, 1120, 165)))
    sc = Shot("conn")
    sc.mark(sc.find("8208HD", cls="Label") if False else sc.find("8208HD"), 4, "tl")
    sc.rect((18, 225, 760, 262), pad=2)
    sc.num(14, 243, 4)
    sc.mark(sc.find("連線中心"), 5, "tl")
    sc.mark(sc.find("⚙ 位址"), 6, "tl")
    parts.append(sc.im.crop((0, 185, 1120, 275)))
    h = sum(p.height for p in parts) + 10 * (len(parts) - 1)
    cv = Image.new("RGB", (1120, h), "white")
    y = 0
    for p in parts:
        cv.paste(p, (0, y))
        y += p.height + 10
    sc.im = cv
    return sc.save("03_dut_conn.png")


def out_trigger():
    s = Shot("trig")
    s.mark(s.find("C8 RSTn") if False else s.find("PY_VAR", cls="Combobox", ymin=300, ymax=330, nth=0), 1)
    s.mark(s.find("PY_VAR", cls="Spinbox"), 2)
    s.mark((s.find("Rising")[0], s.find("Rising")[1], s.find("Either")[2], s.find("Either")[3]), 3)
    s.mark((s.find("PY_VAR", cls="Combobox", ymin=375, ymax=400, nth=0)[0], s.find("PY_VAR", cls="Combobox", ymin=375, ymax=400, nth=0)[1],
            s.find("s", cls="TLabel", ymin=375, ymax=400, xmin=440)[2], s.find("PY_VAR", cls="Combobox", ymin=375, ymax=400, nth=0)[3]), 4)
    s.mark(s.find("套用觸發到兩台"), 5, "tl")
    return s.save("04_trigger.png", (0, 230, 1120, 480))


def out_channels():
    s = Shot("chpm")
    s.mark(s.find("寫入 bus name"), 1, "tl")
    s.mark(s.find("讀回", cls="Button", ymin=300, ymax=340), 2, "tl")
    x0, y0, x1, y1 = s.find("bus name", cls="Label")
    s.mark((50, y0 - 4, 560, 535), 3, "tr")
    s.mark(s.find("預設（每 CH"), 4, "tl")
    s.mark(s.find("寫入示波器"), 5, "tl")
    s.mark(s.find("讀回示波器"), 6, "tl")
    return s.save("05_channels_p.png", (0, 270, 1120, 1040))


def out_save_capture():
    s = Shot("savecap")
    s.mark(s.find("存到"), 1, "tl")
    s.mark((s.find("畫面 PNG")[0], s.find("畫面 PNG")[1], s.find("P 量測 CSV")[2], s.find("P 量測 CSV")[3]), 2, "tl")
    s.mark(s.find("觸發次數 N"), 3, "tl")
    s.mark(s.find("等觸發逾時"), 4, "tl")
    s.mark(s.find("0 / 10"), 5, "tl")
    return s.save("06_save_capture.png", (0, 345, 1120, 675))


def out_seq_cfg():
    s = Shot("seqcfg")
    return s.save("07_seq_settings.png", (0, 660, 1120, 1040)) if False else s.save("07_seq_settings.png", (0, 425, 1120, 1040))


def out_run_full():
    s = Shot("run_stack")
    s.mark(s.find("▶ 擷取＋存檔"), 1, "tr")
    st = s.find("擷取完成", cls="Label") if any("擷取完成" in e["text"] for e in s.g) else None
    s.mark(s.find("擷取完成"), 2, "tl") if st else None
    s.mark(s.find("存檔結果", cls="Label", xmin=1100), 3, "tr") if any("存檔結果" in e["text"] and e["cls"] == "Label" for e in s.g) else None
    return s.save("08_run_result.png", (0, 0, 1920, 1040))


def out_timing_up():
    s = Shot("run_seq_up")
    return s.save("10_timing_up.png", (1125, 100, 1920, 1040))


def out_timing_down():
    s = Shot("run_seq_down")
    return s.save("11_timing_down.png", (1125, 100, 1920, 1040))


def out_merge():
    s = Shot("run_merge")
    return s.save("12_merge.png", (1125, 100, 1920, 1040))


def out_pmeas():
    a = Shot("pm")
    b = Shot("pm")
    return a.save("13_pmeas_table.png", (1125, 280, 1920, 1000))


def out_arb_buttons():
    s = Shot("arb_idle")
    s.mark(s.find("CH1", cls="Button"), 1, "tl")
    s.mark(s.find("CH3", cls="Button"), 2, "tr")
    s.mark((s.find("Ramp", cls="Button")[0], s.find("Ramp", cls="Button")[1], s.find("Instant", cls="Button")[2], s.find("Instant", cls="Button")[3]), 3, "tl")
    s.mark((s.find("標稱")[0], s.find("標稱")[1], 1090, s.find("重複")[3] + 30), 4, "tr")
    s.mark(s.find("ARB 啟動＋擷取", cls="Button"), 5, "tl")
    s.mark(s.find("安全中止"), 6, "tl")
    return s.save("14_arb_buttons.png", (0, 460, 1120, 940))


def _arb(name, out):
    s = Shot(name)
    y0 = s.find("⑧", cls="Label")[1] - 8
    return s.save(out, (0, y0, 1120, 1040))


def out_arb_ramp():
    return _arb("arb_ramp", "15_arb_ramp.png")


def out_arb_brown():
    return _arb("arb_brown", "16_arb_brownout.png")


def out_arb_onoff():
    return _arb("arb_onoff", "17_arb_onoffon.png")


def out_arb_timing():
    s = Shot("arb_brown")
    return s.save("18_arb_timing.png", (1125, 100, 1920, 1040))


def out_noise_cfg():
    s = Shot("noise")
    s.mark(s.find("啟用（擷取時算"), 1, "tl")
    s.mark((26, s.find("穩態等待")[1], 800, s.find("穩態等待")[3]), 2, "tr")
    s.mark((26, s.find("限值（佔穩態")[1], 700, s.find("限值（佔穩態")[3]), 3, "tr")
    s.mark(s.find("預設限值（待確認）的"), 4, "tl")
    s.mark((26, 775, 440, 1040), 5, "tr")
    return s.save("19_noise_settings.png", (0, 538, 1120, 1040))


def out_noise_result():
    s = Shot("noise")
    return s.save("20_noise_result.png", (1125, 250, 1920, 930))


def out_spec_cfg():
    s = Shot("spec_u2")
    return s.save("21_spec_u2.png", (1125, 215, 1920, 900))


def out_spec_m2():
    s = Shot("spec_m2")
    return s.save("22_spec_m2.png", (1125, 215, 1920, 900))


def out_nvme_manual():
    s = Shot("nvme_manual")
    return s.save("23_nvme_manual.png", (0, 320, 1120, 1040))


def out_nvme_auto():
    s = Shot("nvme_auto")
    return s.save("24_nvme_auto.png", (0, 320, 1920, 1040))


def out_nvme_drop():
    s = Shot("nvme_drop")
    return s.save("25_nvme_drop.png", (0, 0, 1920, 330))


def out_continuous():
    s = Shot("cont")
    return s.save("26_continuous.png", (0, 0, 1920, 1040))


def out_unplug():
    s = Shot("unplug")
    return s.save("27_unplug.png", (0, 0, 1920, 330))


def out_folded():
    s = Shot("folded")
    return s.save("28_folded.png", (0, 0, 1920, 1040))


# ---- 以下覆寫前面較簡單的版本（加上更完整的編號標註）
def _caption_strip(text, width):
    im = Image.new("RGB", (width, 34), (255, 247, 237))
    ImageDraw.Draw(im).text((10, 17), text, fill=RED, font=_font(21), anchor="lm")
    return im


def out_dut_conn():
    """DUT 規格列（U.2 / M.2 兩種選法）＋ ① 連線列。"""
    a, b = Shot("dut_u2"), Shot("dut_m2")
    parts = []
    for sh, cap, key in ((a, "選 U.2／U.3　→　標稱 VIN 12 V、電源設計表 U2_12V_…", "U.2／U.3（VIN 12 V）"),
                         (b, "選 M.2　→　標稱 VIN 3.3 V、電源設計表 M2_3V3_…", "M.2（VIN 3.3 V）")):
        sh.mark(sh.find(key), 1, "tl")
        sh.mark(sh.find("PY_VAR", cls="Combobox", ymin=100, ymax=160), 2, "tr")
        sh.mark(sh.find("PASS", cls="Label", ymin=70, ymax=120), 3, "tl")
        parts.append(_caption_strip(cap, 1120))
        parts.append(sh.im.crop((0, 80, 1120, 165)))
    sc = Shot("conn")
    sc.rect((30, 224, 800, 262), pad=2)
    sc.num(30, 224, 4)
    sc.mark(sc.find("連線中心"), 5, "tl")
    sc.mark(sc.find("⚙ 位址"), 6, "tl")
    parts.append(_caption_strip("① 儀器連線：一行看三台儀器（Linux 主機啟用後才會多一個）", 1120))
    parts.append(sc.im.crop((0, 185, 1120, 270)))
    h = sum(p_.height for p_ in parts) + 6 * (len(parts) - 1)
    cv = Image.new("RGB", (1120, h), "white")
    y = 0
    for p_ in parts:
        cv.paste(p_, (0, y))
        y += p_.height + 6
    sc.im = cv
    return sc.save("03_dut_conn.png")


def out_channels():
    s = Shot("chpm")
    s.mark(s.find("寫入 bus name"), 1, "tl")
    s.mark(s.find("讀回", cls="Button", ymin=300, ymax=340), 2, "tl")
    s.mark((26, 385, 560, 540), 3, "r")
    s.mark(s.find("預設（每 CH"), 4, "tl")
    s.mark(s.find("寫入示波器"), 5, "tl")
    s.mark(s.find("讀回示波器"), 6, "tl")
    return s.save("05_channels_p.png", (0, 272, 1120, 1040))


def out_seq_cfg():
    s = Shot("seqcfg")
    y = s.find("方向", cls="Label")[1] if any(e["text"] == "方向" for e in s.g) else 475
    s.mark((26, 468, 160, 502), 1, "tl")
    s.mark((150, 468, 285, 502), 2, "tl")
    s.mark((298, 468, 540, 502), 3, "tl")
    s.mark((24, 510, 700, 560), 4, "r") if False else None
    s.mark((600, 468, 800, 502), 4, "tr")
    s.mark((20, 520, 710, 822), 5, "r")
    return s.save("07_seq_settings.png", (0, 425, 1120, 1040))


def out_run_full():
    s = Shot("run_stack")
    s.mark(s.find("▶ 擷取＋存檔"), 1, "tr")
    s.mark(s.find("擷取完成"), 2, "tl")
    s.mark((1135, 190, 1915, 835), 3, "tl")
    s.mark(s.find("存檔結果", cls="Label", xmin=1100), 4, "tr") if any("存檔結果" in e["text"] and e["cls"] == "Label" for e in s.g) else None
    return s.save("08_run_result.png", (0, 0, 1920, 1040))


def _timing_marks(s):
    s.mark((1138, 232, 1908, 256), 1, "tl")
    s.mark((1138, 260, 1908, 365), 2, "tl")
    s.mark((1235, 388, 1765, 645), 3, "tl")
    s.mark((1775, 388, 1915, 648), 4, "tl")


def out_timing_up():
    s = Shot("run_seq_up")
    _timing_marks(s)
    return s.save("10_timing_up.png", (1125, 100, 1920, 1040))


def out_timing_down():
    s = Shot("run_seq_down")
    _timing_marks(s)
    return s.save("11_timing_down.png", (1125, 100, 1920, 1040))


def out_merge():
    s = Shot("run_merge")
    s.mark((1135, 190, 1915, 213), 1, "tl")
    s.mark((1135, 230, 1915, 590), 2, "tl")
    return s.save("12_merge.png", (1125, 100, 1920, 1040))


def out_pmeas():
    a = Shot("pm")
    a.mark((1138, 330, 1912, 372), 1, "tl")
    a.mark((1138, 375, 1912, 700), 2, "tl")
    return a.save("13_pmeas_table.png", (1125, 280, 1920, 1000))


def _arb(name, out):
    s = Shot(name)
    return s.save(out, (0, 262, 1120, 1040))


def out_arb_timing():
    s = Shot("arb_brown")
    s.mark((1138, 232, 1908, 256), 1, "tl")
    s.mark((1138, 260, 1908, 440), 2, "tl")
    s.mark((1235, 450, 1765, 840), 3, "tl")
    return s.save("18_arb_timing.png", (1125, 100, 1920, 940))


def out_noise_result():
    s = Shot("noise")
    s.mark((1138, 296, 1912, 322), 1, "tl")
    s.mark((1138, 326, 1912, 525), 2, "tl")
    s.mark((1138, 560, 1915, 880), 3, "tl")
    return s.save("20_noise_result.png", (1125, 250, 1920, 930))


def out_spec_cfg():
    s = Shot("spec_u2")
    s.mark((1138, 262, 1700, 290), 1, "tl")
    s.mark((1138, 355, 1915, 570), 2, "tl")
    s.mark((1138, 575, 1915, 880), 3, "tl")
    return s.save("21_spec_u2.png", (1125, 215, 1920, 900))


def out_spec_m2():
    s = Shot("spec_m2")
    s.mark((1138, 262, 1700, 290), 1, "tl")
    s.mark((1138, 355, 1915, 570), 2, "tl")
    return s.save("22_spec_m2.png", (1125, 215, 1920, 900))


def out_nvme_manual():
    s = Shot("nvme_manual")
    y = s.find("啟用 Linux NVMe rescan")
    s.mark(y, 1, "tl")
    s.mark((30, 645, 690, 685), 2, "tr")
    s.mark(s.find("立刻 rescan"), 3, "tl")
    s.mark(s.find("", cls="Treeview"), 4, "tl")
    return s.save("23_nvme_manual.png", (0, 320, 1120, 1040))


def out_nvme_auto():
    s = Shot("nvme_auto")
    try:
        s.mark(s.find("上電後自動 rescan 認盤", cls="Checkbutton"), 1, "tl")
        s.mark(s.find("預期 link", cls="TLabel"), 2, "tl")
        s.mark(s.find("掉盤時停止連續擷取"), 3, "tl")
        s.mark(s.find("rescan 前先 remove"), 4, "tl")
    except KeyError as e:
        print("   (nvme_auto 標註略過)", e)
    s.mark((1135, 760, 1915, 880), 5, "tl") if False else None
    return s.save("24_nvme_auto.png", (0, 320, 1920, 1040))


def out_nvme_drop():
    s = Shot("nvme_drop")
    s.mark((12, 270, 1112, 302), 1, "tl")
    s.mark((1195, 52, 1915, 78), 2, "tr")
    return s.save("25_nvme_drop.png", (0, 0, 1920, 330))


def out_continuous():
    s = Shot("cont")
    s.mark(s.find("觸發次數 N"), 1, "tl")
    s.mark(s.find("3 / 3"), 2, "tl")
    s.mark((1138, 232, 1912, 262), 3, "tl")
    s.mark((1135, 690, 1915, 810), 4, "tl")
    s.mark((1135, 850, 1915, 1035), 5, "tl")
    return s.save("26_continuous.png", (0, 0, 1920, 1040))


def out_unplug():
    s = Shot("unplug")
    s.mark(s.find("6104B", cls="Label", ymin=220, ymax=260), 1, "tl")
    s.mark((14, 262, 700, 298), 2, "tl")
    s.mark((1700, 8, 1912, 48), 3, "tr")
    return s.save("27_unplug.png", (0, 0, 1920, 330))


def out_folded():
    s = Shot("folded")
    s.mark((14, 233, 1112, 262), 1, "tl") if False else None
    s.num(30, 262 - 18 + 0, 1) if False else None
    s.num(40, 268, 1)
    s.num(300, 268, 2)
    s.num(1083, 268, 3)
    s.num(1118, 480, 4)
    s.mark(s.find("» 收合"), 5, "tl")
    return s.save("28_folded.png", (0, 0, 1920, 1040))


# ---- 以元件座標對位的版本（再覆寫一次）
def _big(s, cls, nth=0, minh=40):
    hits = [(e["x"], e["y"], e["x"] + e["w"], e["y"] + e["h"]) for e in s.g
            if e["cls"] == cls and e["x"] >= 1100 and e["h"] >= minh]
    return hits[nth]


def _tv(s, nth=0):
    return _big(s, "Treeview", nth)


def _cv(s, nth=0):
    return _big(s, "Canvas", nth)


def _timing_marks(s):
    tv, cv = _tv(s), _cv(s)
    s.mark((1138, tv[1] - 27, 1908, tv[1] - 3), 1, "tl")
    s.mark(tv, 2, "tl")
    s.mark((1235, cv[1] + 18, 1768, cv[3] - 25), 3, "tl")
    s.mark((1772, cv[1] + 18, 1912, cv[3] - 25), 4, "tl")


def out_seq_cfg():
    s = Shot("seqcfg")
    s.mark((26, 468, 160, 502), 1, "tl")
    s.mark((150, 468, 285, 502), 2, "tl")
    s.mark((298, 468, 540, 502), 3, "tl")
    s.mark((600, 468, 800, 502), 4, "tr")
    s.mark((20, 520, 710, 885), 5, "r")
    return s.save("07_seq_settings.png", (0, 425, 1120, 1040))


def out_merge():
    s = Shot("run_merge")
    s.mark((1138, 190, 1915, 218), 1, "tl")
    s.mark(_cv(s), 2, "tl")
    return s.save("12_merge.png", (1125, 100, 1920, 1000))


def out_pmeas():
    a = Shot("pm")
    tv = _tv(a)
    a.mark((tv[0], tv[1], tv[2], tv[1] + 36), 1, "tl")
    a.mark((tv[0], tv[1] + 38, tv[2], tv[3]), 2, "tl")
    return a.save("13_pmeas_table.png", (1125, 280, 1920, 1000))


def _arb(name, out):
    s = Shot(name)
    return s.save(out, (0, 262, 1120, 1000))


def out_arb_timing():
    s = Shot("arb_brown")
    tv, cv = _tv(s), _cv(s)
    s.mark((1138, tv[1] - 27, 1908, tv[1] - 3), 1, "tl")
    s.mark(tv, 2, "tl")
    s.mark((1235, cv[1] + 18, 1768, cv[3] - 25), 3, "tl")
    return s.save("18_arb_timing.png", (1125, 100, 1920, 940))


def out_noise_result():
    s = Shot("noise")
    tv, cv = _tv(s), _cv(s)
    s.mark((1138, tv[1] - 28, 1912, tv[1] - 3), 1, "tl")
    s.mark(tv, 2, "tl")
    s.mark((1138, cv[1] + 10, 1912, cv[3] - 20), 3, "tl")
    return s.save("20_noise_result.png", (1125, 250, 1920, 930))


def out_spec_cfg():
    s = Shot("spec_u2")
    tv = _tv(s)
    s.mark((1138, 262, 1700, 290), 1, "tl")
    s.mark((1138, tv[1], 1912, tv[1] + 280), 2, "tl")
    s.mark((1138, tv[1] + 290, 1912, tv[3]), 3, "tl")
    return s.save("21_spec_u2.png", (1125, 215, 1920, 900))


def out_spec_m2():
    s = Shot("spec_m2")
    tv = _tv(s)
    s.mark((1138, 262, 1700, 290), 1, "tl")
    s.mark((1138, tv[1], 1912, tv[1] + 280), 2, "tl")
    return s.save("22_spec_m2.png", (1125, 215, 1920, 900))


def out_continuous():
    s = Shot("cont")
    tv, cv = _tv(s), _cv(s)
    s.mark(s.find("觸發次數 N"), 1, "tl")
    s.mark(s.find("3 / 3"), 2, "tl")
    s.mark((1138, tv[1] - 27, 1912, cv[3] - 20), 3, "tl")
    s.mark(_big(s, "Text", 0), 4, "tl")
    s.mark(_big(s, "Text", 1), 5, "tl")
    return s.save("26_continuous.png", (0, 0, 1920, 1040))


def out_wiring():
    """儀器接線示意圖（Pillow 繪製，不用截圖）。"""
    W, H = 1700, 940
    im = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(im)
    f_t, f_b, f_s = _font(30), _font(24, False), _font(21, False)
    BL, GR, OR, GY = (14, 116, 189), (21, 128, 61), (194, 65, 12), (100, 116, 139)

    def box(x0, y0, x1, y1, title, lines, col):
        d.rounded_rectangle((x0, y0, x1, y1), radius=14, outline=col, width=4, fill=(248, 250, 252))
        d.rectangle((x0 + 2, y0 + 2, x1 - 2, y0 + 48), fill=col)
        d.text(((x0 + x1) // 2, y0 + 26), title, fill="white", font=f_t, anchor="mm")
        y = y0 + 62
        for ln in lines:
            d.text((x0 + 18, y), ln, fill=(30, 41, 59), font=f_b)
            y += 33

    def arrow(p0, p1, col, label="", w=5, off=(0, -26)):
        d.line([p0, p1], fill=col, width=w)
        import math
        a = math.atan2(p1[1] - p0[1], p1[0] - p0[0])
        for sg in (-1, 1):
            d.line([p1, (p1[0] - 18 * math.cos(a) + sg * 9 * math.sin(a), p1[1] - 18 * math.sin(a) - sg * 9 * math.cos(a))], fill=col, width=w)
        if label:
            d.text(((p0[0] + p1[0]) // 2 + off[0], (p0[1] + p1[1]) // 2 + off[1]), label, fill=col, font=f_s, anchor="mm")

    box(600, 20, 1100, 150, "中控電腦（ScopeWaveSaver）", ["3 條 USB（兩台示波器＋N6705）", "選用：網路 SSH → Linux 主機"], BL)
    box(40, 330, 560, 700, "MASTER  WaveRunner 8208HD",
        ["C1 CORE     C2 DDRVPP", "C3 GPIO     C4 DDRIO", "C5 PHYIO    C6 NFIO", "C7 NFCORE   C8 RSTn（觸發）", "", "Aux Out ─▶ 接輔機 EXT（BNC）"], GR)
    box(640, 330, 1060, 640, "SLAVE  HDO6104B",
        ["C1 VIN      C2 VBUS", "C3 BUSPG    C4 IBUS（電流 A）", "", "EXT ◀─ 主機 Aux Out（BNC）"], GR)
    box(1140, 330, 1660, 520, "Keysight N6705", ["CH3 ─▶ DUT VIN（預設）", "CH1 ─▶ 也可選（ARB 一次只用一個）"], OR)
    box(640, 760, 1260, 910, "DUT（U.2／U.3 或 M.2 SSD）", ["電源輸入 VIN：12 V（U.2）／3.3 V（M.2）", "各電源 rail、RSTn、BUSPG 的測試點"], GY)
    box(1320, 700, 1660, 880, "Linux 主機（選用）", ["裝 DUT 的主機", "PCIe rescan／nvme list"], GY)
    # 電腦 ↔ 儀器 USB
    arrow((700, 150), (300, 330), BL, "USB")
    arrow((850, 150), (850, 330), BL, "USB", off=(30, 0))
    arrow((1030, 150), (1400, 330), BL, "USB")
    # 主 Aux Out → 輔 EXT
    arrow((560, 580), (640, 580), RED, "", w=6)
    d.text((600, 612), "BNC", fill=RED, font=f_s, anchor="mm")
    # N6705 → DUT
    arrow((1400, 520), (1100, 760), OR, "電源線（VIN）", off=(70, 10))
    # 探棒
    arrow((640, 790), (300, 700), GR, "探棒：各 rail、RSTn", off=(-10, 28))
    arrow((760, 760), (800, 640), GR, "探棒：VIN／VBUS／BUSPG／IBUS", off=(190, 0))
    # Linux
    arrow((1320, 800), (1260, 830), GY, "", w=4)
    d.line([(1100, 100), (1600, 100), (1600, 695)], fill=GY, width=3)
    d.text((1350, 80), "SSH（選用）", fill=GY, font=f_s, anchor="mm")
    sh = Shot.__new__(Shot)
    sh.im = im
    return sh.save("02_wiring.png")


OUTS = [out_overview, out_wiring, out_toolbar, out_dut_conn, out_trigger, out_channels, out_save_capture, out_seq_cfg, out_run_full,
        out_timing_up, out_timing_down, out_merge, out_pmeas, out_arb_buttons, out_arb_ramp, out_arb_brown, out_arb_onoff,
        out_arb_timing, out_noise_cfg, out_noise_result, out_spec_cfg, out_spec_m2, out_nvme_manual, out_nvme_auto,
        out_nvme_drop, out_continuous, out_unplug, out_folded]

if __name__ == "__main__":
    main()
