"""子視窗：收合（▾/▸）、拖標題排序、拖分隔條改大小、⧉ 獨立視窗；版面寫入 ui_layout.json。"""
from __future__ import annotations

import json
import os
import tkinter as tk
from tkinter import ttk

CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮"
SASH_H = 6
MIN_BODY = 80


def layout_path(override=""):
    if override:
        return override
    env = os.environ.get("SCOPE_UI_LAYOUT", "").strip()
    if env:
        return env
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "scope_wave_saver", "ui_layout.json")
    return os.path.join(os.path.expanduser("~"), ".config", "scope_wave_saver", "ui_layout.json")


# v1：左欄 fold 卡片含 ⑨ 選擇列＋表。v2：DUT 規格選擇列改放 ① 上方固定列；「spec」只剩判比表。
LAYOUT_VERSION = 2


class LayoutStore:
    """記住收合狀態、順序、分隔條位置。"""

    def __init__(self, path=""):
        self.path = layout_path(path)
        self.data = {"version": LAYOUT_VERSION, "collapsed": {}, "order": {}, "sash": {}, "weights": {}}
        self.enabled = bool(self.path) and self.path != "-"
        if self.enabled:
            self.load()

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                self.data.update(d)
                for k in ("collapsed", "order", "sash", "weights"):
                    if not isinstance(self.data.get(k), dict):
                        self.data[k] = {}
        except (OSError, json.JSONDecodeError, TypeError):
            pass
        self._migrate()

    def _migrate(self):
        """舊 ui_layout.json 升到目前版：選擇列已不在左欄 fold 裡，不重寫使用者拖過的卡片順序。"""
        try:
            v = int(self.data.get("version") or 1)
        except (TypeError, ValueError):
            v = 1
        if v >= LAYOUT_VERSION:
            return
        # v1 → v2：DUT 規格（U.2／M.2＋機種）移到 ① 上方固定列；left.order 的 spec 仍是判比表卡片
        self.data["version"] = LAYOUT_VERSION
        if self.enabled:
            self.save()

    def save(self):
        if not self.enabled:
            return
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def collapsed(self, key, default=False):
        v = self.data["collapsed"].get(key)
        return default if v is None else bool(v)

    def set_collapsed(self, key, on):
        self.data["collapsed"][key] = bool(on)

    def order(self, group, default):
        got = self.data["order"].get(group)
        if not isinstance(got, list):
            return list(default)
        known, rest = [k for k in got if k in default], [k for k in default if k not in got]
        return known + rest

    def set_order(self, group, keys):
        self.data["order"][group] = list(keys)

    def sash(self, key, default=None):
        v = self.data["sash"].get(key)
        try:
            return int(v) if v is not None else default
        except (TypeError, ValueError):
            return default

    def set_sash(self, key, val):
        try:
            self.data["sash"][key] = int(val)
        except (TypeError, ValueError):
            pass

    def weights(self, group, default):
        got = self.data["weights"].get(group)
        if not isinstance(got, dict):
            return dict(default)
        out = dict(default)
        out.update({k: float(v) for k, v in got.items() if _is_num(v)})
        return out

    def set_weights(self, group, w):
        self.data["weights"][group] = {k: float(v) for k, v in w.items()}


def _is_num(v):
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def numbered(num, title):
    if not num:
        return title
    return f"{CIRCLED[num - 1]} {title}"


class FoldCard(ttk.Frame):
    """標題列可點 ▾/▸ 收合、拖曳排序、⧉ 拆成獨立視窗。內容放 .body。"""

    def __init__(self, master, key, title, store=None, default_open=True, detachable=True,
                 on_change=None, tip=None):
        super().__init__(master)
        self.key, self.title, self.store = key, title, store
        self.on_change = on_change
        self.detachable = detachable
        self.stack = None
        self.win = None
        self._drag = None
        opened = not (store.collapsed(key, default=not default_open) if store else (not default_open))
        self.opened = opened
        self.header = tk.Frame(self, bg="#eaeef2", highlightbackground="#d0d7de", highlightthickness=1,
                               cursor="hand2")
        self.header.pack(fill="x")
        self.arrow = tk.Label(self.header, text="▾" if opened else "▸", bg="#eaeef2", fg="#0E9BD8",
                              font=(None, 11, "bold"), cursor="hand2", padx=6)
        self.arrow.pack(side="left")
        self.lb = tk.Label(self.header, text=title, bg="#eaeef2", fg="#1f2328",
                           font=(None, 10, "bold"), anchor="w", cursor="hand2")
        self.lb.pack(side="left", fill="x", expand=True, pady=3)
        self._ph = tk.Label(self.header, text="", bg="#eaeef2", fg="#57606a", font=(None, 9))
        if detachable:
            self.btn_pop = tk.Button(self.header, text="⧉", command=self.toggle_detach, relief="flat",
                                     bg="#eaeef2", fg="#0969da", bd=0, padx=6, cursor="hand2",
                                     font=(None, 10, "bold"))
            self.btn_pop.pack(side="right")
            if tip:
                tip(self.btn_pop, "獨立視窗（可拉大、拖到第二個螢幕）；關閉或再按一次就放回")
        self.body = ttk.Frame(self, padding=(8, 4, 8, 6))
        if opened:
            self.body.pack(fill="both", expand=True)
        for w in (self.header, self.arrow, self.lb):
            w.bind("<Button-1>", self._press, add="+")
            w.bind("<B1-Motion>", self._motion, add="+")
            w.bind("<ButtonRelease-1>", self._release, add="+")
        if tip:
            tip(self.header, "點 ▾/▸ 收合；拖標題可改順序；⧉ 獨立視窗")
        self._refresh()

    def header_h(self):
        self.header.update_idletasks()
        return max(self.header.winfo_reqheight(), 26)

    def _refresh(self):
        detached = self.win is not None
        self.arrow.configure(text="▾" if self.opened and not detached else "▸")
        self.lb.configure(foreground="#57606a" if (not self.opened or detached) else "#1f2328")
        if detached:
            self._ph.configure(text="  （已獨立視窗 — 再按 ⧉ 或關窗放回）")
            if not self._ph.winfo_ismapped():
                self._ph.pack(side="left", before=getattr(self, "btn_pop", None) or self._ph)
            if self.body.winfo_manager() == "pack" and self.body.nametowidget(self.body.winfo_parent()) is self:
                self.body.pack_forget()
        else:
            self._ph.pack_forget()
            if self.opened:
                if self.body.winfo_manager() != "pack" or self.body.nametowidget(self.body.winfo_parent()) is self:
                    self.body.pack(fill="both", expand=True)
            else:
                if self.body.winfo_manager() == "pack" and self.body.nametowidget(self.body.winfo_parent()) is self:
                    self.body.pack_forget()
        if self.store:
            self.store.set_collapsed(self.key, not self.opened)

    def set_open(self, on, notify=True):
        on = bool(on)
        if self.opened == on:
            if notify and self.on_change:
                self.on_change(self)
            return
        self.opened = on
        self._refresh()
        if notify and self.on_change:
            self.on_change(self)

    def toggle(self):
        if self.win is not None:
            self.dock()
            return
        self.set_open(not self.opened)

    def _press(self, e):
        self._drag = {"x": e.x_root, "y": e.y_root, "moved": False}

    def _motion(self, e):
        if not self._drag:
            return
        if abs(e.y_root - self._drag["y"]) + abs(e.x_root - self._drag["x"]) > 8:
            self._drag["moved"] = True
            if self.stack is not None:
                self.stack.drag_to(self, e.y_root)

    def _release(self, e):
        d = self._drag
        self._drag = None
        if not d:
            return
        if d["moved"]:
            if self.stack is not None:
                self.stack.drop(self, e.y_root)
            return
        self.toggle()

    def toggle_detach(self):
        if self.win is not None:
            self.dock()
        else:
            self.pop()

    def pop(self):
        if self.win is not None or not self.detachable:
            return
        self.body.pack_forget()
        w = tk.Toplevel(self.winfo_toplevel())
        w.title(self.title)
        w.minsize(420, 240)
        self.body.pack(in_=w, fill="both", expand=True)
        w.protocol("WM_DELETE_WINDOW", self.dock)
        try:
            top = self.winfo_toplevel()
            w.geometry(f"900x640+{top.winfo_rootx() + 80}+{top.winfo_rooty() + 60}")
        except tk.TclError:
            pass
        self.win = w
        self._refresh()
        if self.on_change:
            self.on_change(self)

    def dock(self):
        w = self.win
        if w is None:
            return
        self.win = None
        try:
            self.body.pack_forget()
        except tk.TclError:
            pass
        try:
            w.destroy()
        except tk.TclError:
            pass
        self.opened = True
        self._refresh()
        if self.on_change:
            self.on_change(self)


class FoldStack(ttk.Frame):
    """左欄：依序 pack，可拖標題改順序（高度隨內容；外層自己捲動）。"""

    def __init__(self, master, store=None, group="left", **kw):
        super().__init__(master, **kw)
        self.store, self.group = store, group
        self.panes = []

    def add(self, card: FoldCard):
        self.panes.append(card)
        card.stack = self
        card.on_change = self._changed
        self._apply_saved_order()
        self.relayout()
        return card

    def _apply_saved_order(self):
        if not self.store:
            return
        order = self.store.order(self.group, [c.key for c in self.panes])
        self.panes.sort(key=lambda c: order.index(c.key) if c.key in order else 999)

    def keys(self):
        return [c.key for c in self.panes]

    def by_key(self, key):
        for c in self.panes:
            if c.key == key:
                return c
        return None

    def relayout(self):
        for c in self.panes:
            c.pack_forget()
        for c in self.panes:
            c.pack(fill="x", pady=(0, 4))
        if self.store:
            self.store.set_order(self.group, self.keys())

    def _changed(self, _card=None):
        self.relayout()

    def drag_to(self, card, y_root):
        tgt = self._at(y_root)
        if tgt is None or tgt is card:
            return

    def drop(self, card, y_root):
        tgt = self._at(y_root)
        if tgt is None or tgt is card:
            return
        self.panes.remove(card)
        i = self.panes.index(tgt)
        mid = tgt.winfo_rooty() + tgt.winfo_height() / 2
        self.panes.insert(i if y_root < mid else i + 1, card)
        self.relayout()

    def _at(self, y_root):
        for c in self.panes:
            y0, y1 = c.winfo_rooty(), c.winfo_rooty() + c.winfo_height()
            if y0 <= y_root <= y1:
                return c
        if self.panes:
            if y_root < self.panes[0].winfo_rooty():
                return self.panes[0]
            return self.panes[-1]
        return None


class SashStack(ttk.Frame):
    """右欄：展開的子視窗用分隔條拉高度；收合的只留標題列。"""

    def __init__(self, master, store=None, group="right", defaults=None, **kw):
        super().__init__(master, **kw)
        self.store, self.group = store, group
        self.panes = []
        self.defaults = dict(defaults or {})
        self._weights = dict(self.defaults)
        if store:
            self._weights = store.weights(group, self._weights)
        self._sashes = []
        self._drag = None
        self.bind("<Configure>", lambda _e: self._place())

    def add(self, card: FoldCard, weight=1.0):
        self.panes.append(card)
        card.stack = self
        card.on_change = self._changed
        self._weights.setdefault(card.key, float(weight))
        if self.store:
            order = self.store.order(self.group, [c.key for c in self.panes])
            self.panes.sort(key=lambda c: order.index(c.key) if c.key in order else 999)
        self._place()
        return card

    def by_key(self, key):
        for c in self.panes:
            if c.key == key:
                return c
        return None

    def keys(self):
        return [c.key for c in self.panes]

    def remove(self, key):
        c = self.by_key(key)
        if not c:
            return
        self.panes.remove(c)
        try:
            c.place_forget()
        except tk.TclError:
            pass
        self._place()

    def _changed(self, _c=None):
        if self.store:
            self.store.set_order(self.group, self.keys())
            self.store.set_weights(self.group, self._weights)
        self._place()

    def drag_to(self, card, y_root):
        pass

    def drop(self, card, y_root):
        tgt = None
        for c in self.panes:
            try:
                if c.winfo_rooty() <= y_root <= c.winfo_rooty() + max(c.winfo_height(), 8):
                    tgt = c
                    break
            except tk.TclError:
                continue
        if tgt is None or tgt is card:
            return
        self.panes.remove(card)
        i = self.panes.index(tgt)
        mid = tgt.winfo_rooty() + max(tgt.winfo_height(), 1) / 2
        self.panes.insert(i if y_root < mid else i + 1, card)
        self._changed()

    def _place(self):
        W, H = max(self.winfo_width(), 1), max(self.winfo_height(), 1)
        for s in self._sashes:
            try:
                s.destroy()
            except tk.TclError:
                pass
        self._sashes = []
        opened = [c for c in self.panes if c.opened and c.win is None]
        head_total = sum(c.header_h() for c in self.panes)
        n_sash = max(len(opened) - 1, 0)
        rest = max(H - head_total - n_sash * SASH_H, 0)
        tw = sum(max(self._weights.get(c.key, 1.0), 0.2) for c in opened) or 1.0
        bodies = {}
        if opened:
            raw = [rest * max(self._weights.get(c.key, 1.0), 0.2) / tw for c in opened]
            if rest >= MIN_BODY * len(opened):
                for i, h in enumerate(raw):
                    if h < MIN_BODY:
                        raw[i] = MIN_BODY
                extra = sum(raw) - rest
                if extra > 0:
                    flex = [i for i, h in enumerate(raw) if h > MIN_BODY]
                    room = sum(raw[i] - MIN_BODY for i in flex) or 1.0
                    for i in flex:
                        raw[i] -= extra * (raw[i] - MIN_BODY) / room
            bodies = {c.key: max(int(round(h)), 0) for c, h in zip(opened, raw)}
            leftover = rest - sum(bodies.values())
            if leftover and opened:
                bodies[opened[-1].key] += leftover
        y = 0
        prev_open = None
        for c in self.panes:
            hh = c.header_h()
            bh = bodies.get(c.key, 0)
            if prev_open is not None and bh:
                self._mk_sash(prev_open, c, y, W)
                y += SASH_H
            c.place(x=0, y=y, width=W, height=hh + bh)
            y += hh + bh
            if bh:
                prev_open = c
        if self.store:
            self.store.set_order(self.group, self.keys())
            self.store.set_weights(self.group, self._weights)

    def _mk_sash(self, a, b, y, W):
        s = tk.Frame(self, bg="#8c959f", cursor="sb_v_double_arrow", height=SASH_H)
        s.place(x=0, y=max(y, 0), width=W, height=SASH_H)
        s.lift()

        def press(e, a=a, b=b):
            self._drag = {"a": a, "b": b, "y": e.y_root, "wa": self._weights.get(a.key, 1.0),
                          "wb": self._weights.get(b.key, 1.0)}

        def move(e):
            d = self._drag
            if not d:
                return
            dy = e.y_root - d["y"]
            H = max(self.winfo_height(), 1)
            # convert pixels to weight
            opened = [c for c in self.panes if c.opened and c.win is None]
            tw = sum(max(self._weights.get(c.key, 1.0), 0.2) for c in opened) or 1.0
            rest = max(H - sum(c.header_h() for c in self.panes if c.win is None) - max(len(opened) - 1, 0) * SASH_H, 1)
            dw = dy / rest * tw
            na, nb = d["wa"] + dw, d["wb"] - dw
            if na < 0.2 or nb < 0.2:
                return
            self._weights[d["a"].key] = na
            self._weights[d["b"].key] = nb
            self._place()
            self._drag["y"] = e.y_root
            self._drag["wa"], self._drag["wb"] = na, nb

        def rel(_e):
            self._drag = None
            if self.store:
                self.store.set_weights(self.group, self._weights)
                self.store.save()

        s.bind("<Button-1>", press)
        s.bind("<B1-Motion>", move)
        s.bind("<ButtonRelease-1>", rel)
        self._sashes.append(s)


def adapt_section(box, key, title, store=None, default_open=True, tip=None, on_change=None):
    """把 UnifiedBody 的 ① section 加上 ▾/▸ 收合（不動原內容）。"""
    body = None
    hd = None
    for w in box.pack_slaves():
        if getattr(w, "header", None) is w or False:
            pass
    # section 結構：box → hd, body；body.header = hd
    slaves = box.pack_slaves()
    if len(slaves) >= 2:
        hd, body = slaves[0], slaves[1]
    elif slaves:
        hd = slaves[0]
        body = getattr(slaves[0], "header", None) and None
    if body is None:
        # body.conn is the content; its header is on the first slave
        return None
    opened = not (store.collapsed(key, default=not default_open) if store else (not default_open))
    arrow = tk.Label(hd, text="▾" if opened else "▸", fg="#0E9BD8", font=(None, 11, "bold"),
                     cursor="hand2")
    first = hd.pack_slaves()[0] if hd.pack_slaves() else None
    if first:
        arrow.pack(side="left", before=first, padx=(0, 4))
    else:
        arrow.pack(side="left", padx=(0, 4))
    state = {"opened": opened, "body": body, "arrow": arrow, "key": key}

    def refresh():
        on = state["opened"]
        arrow.configure(text="▾" if on else "▸")
        if on:
            if not body.winfo_ismapped():
                body.pack(fill="x")
        else:
            body.pack_forget()
        if store:
            store.set_collapsed(key, not on)

    def toggle(_e=None):
        state["opened"] = not state["opened"]
        refresh()
        if on_change:
            on_change(state)
        if store:
            store.save()

    refresh()
    for w in (hd, arrow, *hd.pack_slaves()):
        if isinstance(w, ttk.Button) or isinstance(w, tk.Button):
            continue
        w.bind("<Button-1>", toggle, add="+")
        w.configure(cursor="hand2") if isinstance(w, (tk.Label, tk.Frame)) else None
    if tip:
        tip(hd, "點 ▾/▸ 收合這一列")
    state["toggle"] = toggle
    state["set_open"] = lambda on: (state.update(opened=bool(on)), refresh())
    return state
