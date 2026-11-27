# -*- coding: utf-8 -*-
"""存檔畫面「照目前顯示」：並排 / 上下 / 合一張 → 一張 PNG（PIL，不需要 Tk，可單獨測試）

  side  並排：左 = 輔 SLAVE 實圖、右 = 主 MASTER 實圖（完整 SCREEN_DUMP，高度對齊），上方一條標題
  stack 上下：兩張實圖各自裁到「格線區」（grid），寬度對齊後直接上下接在一起（中間沒有間隔），
        上方一條標題、下方一條通道 / 時基說明（因為下方 descriptor 方框被裁掉）
  merge 合一張：PC 用兩台讀回的波形在同一時間軸畫的圖（matplotlib Figure → PNG）
格線區：先自動偵測（整列 / 整欄幾乎都是亮點的格線外框），偵測不到用各型號比例 GRID_FRAC（未實機確認，可改）。
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont

LAYOUTS = {"side": "並排", "stack": "上下", "merge": "合一張"}
# (左, 上, 右, 下) 比例；示意圖 = demo_screens 的格線位置。實機 MAUI 畫面位置待確認（自動偵測優先）
GRID_FRAC = {"8208HD": (0.01, 0.04, 0.99, 0.80), "6104B": (0.01, 0.04, 0.99, 0.80)}
HEAD_BG, HEAD_FG = (14, 155, 216), (255, 255, 255)


def _font(size):
    for f in ("C:/Windows/Fonts/msjhbd.ttc", "C:/Windows/Fonts/msjh.ttc",
              "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"):
        if os.path.exists(f):
            try:
                return ImageFont.truetype(f, size)
            except OSError:
                pass
    return ImageFont.load_default()


def grid_box(img, model=""):
    """→ (l, t, r, b) 像素：格線區外框。自動偵測：最上 / 最下 一條「整列 ≥ 85 % 是灰色格線點」的列（只看上方 90 %），欄同理"""
    rgb = img.convert("RGB")
    w, h = rgb.size
    src = rgb.load()
    step = max(1, w // 400)

    class _P:                                   # 「灰色格線點」：夠亮且近乎無彩（通道色的 descriptor 方框不算）
        def __getitem__(self, xy):
            r, g_, b = src[xy]
            return 255 if max(r, g_, b) > 40 and max(r, g_, b) - min(r, g_, b) < 24 else 0
    px = _P()

    def row_full(y):
        n = sum(1 for x in range(0, w, step) if px[x, y] > 40)
        return n >= 0.85 * (w / step)
    rows = [y for y in range(int(h * 0.02), int(h * 0.9)) if row_full(y)]
    if len(rows) >= 2 and rows[-1] - rows[0] > h * 0.3:
        t, b = rows[0], rows[-1]
        hs = max(1, (b - t) // 300)
        cols = [x for x in range(w) if sum(1 for y in range(t, b, hs) if px[x, y] > 40) >= 0.85 * ((b - t) / hs)]
        if len(cols) >= 2 and cols[-1] - cols[0] > w * 0.3:
            return cols[0], t, cols[-1] + 1, b + 1
    fl, ft, fr, fb = GRID_FRAC.get(model, (0.0, 0.0, 1.0, 1.0))
    return int(fl * w), int(ft * h), int(fr * w), int(fb * h)


def _strip(width, text, h=34, right=""):
    im = Image.new("RGB", (width, h), HEAD_BG)
    d = ImageDraw.Draw(im)
    f = _font(int(h * 0.55))
    d.text((10, h * 0.18), text, fill=HEAD_FG, font=f)
    if right:
        tw = d.textlength(right, font=f)
        d.text((width - tw - 10, h * 0.18), right, fill=HEAD_FG, font=f)
    return im


def _tag(im, text, xy=(8, 6)):
    d = ImageDraw.Draw(im)
    f = _font(max(14, im.height // 28))
    x, y = xy
    tw = d.textlength(text, font=f)
    d.rectangle((x - 4, y - 2, x + tw + 6, y + f.size + 6), fill=(0, 0, 0))
    d.text((x, y), text, fill=(120, 200, 255), font=f)


def side(slave, master, title="", right=""):
    """左 輔、右 主（完整畫面，高度對齊）"""
    h = max(slave.height, master.height)
    s = slave.resize((int(slave.width * h / slave.height), h), Image.LANCZOS)
    m = master.resize((int(master.width * h / master.height), h), Image.LANCZOS)
    body = Image.new("RGB", (s.width + m.width, h), (0, 0, 0))
    body.paste(s, (0, 0))
    body.paste(m, (s.width, 0))
    head = _strip(body.width, title or "左：輔 SLAVE　｜　右：主 MASTER", right=right)
    out = Image.new("RGB", (body.width, head.height + h), (0, 0, 0))
    out.paste(head, (0, 0))
    out.paste(body, (0, head.height))
    return out


def stack(slave, master, slave_model="6104B", master_model="8208HD", title="", right="", legend="", width=None,
          tags=("輔 SLAVE", "主 MASTER")):
    """上 輔、下 主：各裁到格線區、寬度一樣、中間不留縫 → 一張連續的圖"""
    gs = slave.crop(grid_box(slave, slave_model))
    gm = master.crop(grid_box(master, master_model))
    w = width or max(gs.width, gm.width)
    gs = gs.resize((w, int(gs.height * w / gs.width)), Image.LANCZOS)
    gm = gm.resize((w, int(gm.height * w / gm.width)), Image.LANCZOS)
    if tags:
        _tag(gs, f"{tags[0]}  {slave_model}")
        _tag(gm, f"{tags[1]}  {master_model}")
    head = _strip(w, title or "上：輔 SLAVE　／　下：主 MASTER（時間格對齊）", right=right)
    foot = _strip(w, legend, h=30) if legend else None
    H = head.height + gs.height + gm.height + (foot.height if foot else 0)
    out = Image.new("RGB", (w, H), (0, 0, 0))
    y = 0
    for part in (head, gs, gm) + ((foot,) if foot else ()):
        out.paste(part, (0, y))
        y += part.height
    return out


def compose(layout, slave_png=None, master_png=None, fig=None, **kw):
    """→ PIL Image。layout = side / stack / merge（merge 要 fig = matplotlib Figure）"""
    if layout == "merge":
        if fig is None:
            raise ValueError("合一張需要 PC 合成波形圖（Figure）")
        import io
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=110, facecolor="white")
        buf.seek(0)
        return Image.open(buf).convert("RGB")
    s, m = Image.open(slave_png).convert("RGB"), Image.open(master_png).convert("RGB")
    if layout == "stack":
        return stack(s, m, **kw)
    kw.pop("legend", None)
    kw.pop("slave_model", None)
    kw.pop("master_model", None)
    return side(s, m, **kw)


def save(path, layout, slave_png=None, master_png=None, fig=None, **kw):
    im = compose(layout, slave_png, master_png, fig, **kw)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    im.save(path)
    return path
