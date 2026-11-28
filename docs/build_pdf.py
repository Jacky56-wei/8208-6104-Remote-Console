# -*- coding: utf-8 -*-
"""把 docs/操作手冊.md 轉成 docs/操作手冊.pdf（圖、表、中文字型都嵌入）。

流程：mermaid 區塊 → mmdc 轉 PNG → python-markdown 轉 HTML → 兩次回圈
      （Chrome／Puppeteer 列印，寫入目錄頁碼、頁首與頁碼）。
需要：google-chrome（或設環境變數 CHROME）、mermaid-cli（mmdc；或設環境變數 MMDC）、
      Node 的 puppeteer（與 mermaid-cli 同套）、python 套件 markdown、
      字型 Noto Sans CJK TC、poppler-utils（pdftotext／pdfinfo，用來對目錄頁碼）。
用法：python3 docs/build_pdf.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "操作手冊.md")
OUT = os.path.join(HERE, "操作手冊.pdf")
BUILD = os.path.join(HERE, "_build")
TITLE = "ScopeWaveSaver 功能介面操作手冊"

# 從 Markdown 目錄區段抽走，改由 PDF 產生帶頁碼的目錄。
TOC_RE = re.compile(r"\n\*\*目錄\*\*\n.*?\n---\n", re.S)


def find_chrome():
    p = os.environ.get("CHROME")
    if p:
        return p
    for c in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        w = shutil.which(c)
        if w:
            return w
    sys.exit("找不到 Chrome／Chromium（可設環境變數 CHROME）")


def find_mmdc():
    p = os.environ.get("MMDC")
    if p:
        return p
    w = shutil.which("mmdc")
    if w:
        return w
    for c in (
        "/workspace/mmdc/node_modules/.bin/mmdc",
        os.path.expanduser("~/node_modules/.bin/mmdc"),
    ):
        if os.path.exists(c):
            return c
    sys.exit("找不到 mmdc（npm i @mermaid-js/mermaid-cli，或設環境變數 MMDC）")


def find_puppeteer_root():
    """mermaid-cli 安裝目錄底下的 puppeteer，或可被 Node 解析的套件。"""
    mmdc = find_mmdc()
    cand = os.path.normpath(os.path.join(os.path.dirname(mmdc), "..", "puppeteer"))
    if os.path.isdir(cand):
        return os.path.dirname(cand)  # node_modules
    # 往上找 node_modules/puppeteer
    cur = os.path.abspath(os.path.dirname(mmdc))
    for _ in range(6):
        nm = os.path.join(cur, "node_modules", "puppeteer")
        if os.path.isdir(nm):
            return os.path.join(cur, "node_modules")
        cur = os.path.dirname(cur)
    sys.exit("找不到 puppeteer（請與 mermaid-cli 一併安裝）")


def slugify(value, sep="-"):
    """同 GitHub 的標題錨點：小寫、去標點（保留中文與連字號）、空白換 -。"""
    v = value.strip().lower()
    v = re.sub(r"[^\w\- ]", "", v)
    return re.sub(r"\s", sep, v)


def render_mermaid(md):
    os.makedirs(os.path.join(BUILD, "diagrams"), exist_ok=True)
    chrome, mmdc = find_chrome(), find_mmdc()
    pcfg = os.path.join(BUILD, "puppeteer.json")
    json.dump({"executablePath": chrome, "args": ["--no-sandbox", "--disable-gpu"]}, open(pcfg, "w"))
    mcfg = os.path.join(BUILD, "mermaid.json")
    json.dump(
        {
            "theme": "default",
            "themeVariables": {
                "fontFamily": "Noto Sans CJK TC, sans-serif",
                "fontSize": "18px",
            },
            "flowchart": {"htmlLabels": True, "useMaxWidth": False, "padding": 12, "nodeSpacing": 28, "rankSpacing": 36},
        },
        open(mcfg, "w"),
    )
    n = [0]

    def sub(m):
        n[0] += 1
        body = m.group(1)
        mmd = os.path.join(BUILD, "diagrams", f"d{n[0]}.mmd")
        png = os.path.join(BUILD, "diagrams", f"d{n[0]}.png")
        open(mmd, "w", encoding="utf-8").write(body)
        # 直向流程圖用適中寬度；含橫列（direction LR）或本來就是 LR 的用寬圖。
        is_wide = ("direction LR" in body) or bool(re.search(r"flowchart\s+LR", body))
        width = "1600" if is_wide else "900"
        cmd = [mmdc, "-p", pcfg, "-c", mcfg, "-i", mmd, "-o", png, "-b", "white", "-s", "3", "-w", width]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 or not os.path.exists(png):
            sys.exit(f"mermaid 第 {n[0]} 張轉換失敗：\n{r.stdout}\n{r.stderr}")
        return f"![流程圖](diagrams/d{n[0]}.png)"

    out = re.sub(r"```mermaid\n(.*?)```", sub, md, flags=re.S)
    print(f"mermaid：{n[0]} 張")
    return out


CSS = r"""
:root {
  --text: #1f2328;
  --muted: #57606a;
  --line: #d0d7de;
  --accent: #0b6aa2;
  --thead: #e7f2f9;
  --thead-border: #b7c9d6;
}
html {
  font-family: "Noto Sans CJK TC", "Noto Sans CJK SC", sans-serif;
  font-size: 11pt;
  color: var(--text);
  -webkit-print-color-adjust: exact;
  print-color-adjust: exact;
}
body {
  line-height: 1.7;
  margin: 0;
  padding: 0;
}
h1 {
  font-size: 21pt;
  line-height: 1.3;
  font-weight: 700;
  border-bottom: 3px solid #0e9bd8;
  padding-bottom: 6px;
  margin: 0 0 10px;
}
h2 {
  font-size: 16pt;
  line-height: 1.35;
  font-weight: 700;
  border-bottom: 2px solid var(--line);
  padding-bottom: 5px;
  margin: 0 0 14px;
  break-after: avoid;
  page-break-after: avoid;
}
h2.chapter {
  break-before: page;
  page-break-before: always;
}
h3 {
  font-size: 13pt;
  line-height: 1.4;
  font-weight: 700;
  color: var(--accent);
  margin: 1.25em 0 0.55em;
  break-after: avoid;
  page-break-after: avoid;
}
h2 + p, h3 + p, h2 + ul, h3 + ul, h2 + ol, h3 + ol {
  break-before: avoid;
  page-break-before: avoid;
}
p {
  margin: 0 0 0.65em;
  orphans: 3;
  widows: 3;
}
ul, ol {
  margin: 0.35em 0 0.9em;
  padding-left: 1.6em;
}
li {
  margin: 0.28em 0;
  orphans: 3;
  widows: 3;
}
li > ul, li > ol { margin: 0.15em 0 0.25em; }

/* 圖＋圖說成組，不拆頁；高度限制避免把上一頁留一大塊空白 */
figure {
  margin: 12px auto 16px;
  text-align: center;
  break-inside: avoid;
  page-break-inside: avoid;
  max-width: 100%;
}
figure img {
  display: block;
  margin: 0 auto;
  max-width: 100%;
  max-height: 145mm;
  width: auto;
  height: auto;
  border: 1px solid var(--line);
  border-radius: 4px;
}
figure.diagram img {
  max-height: 200mm;
  border: 1px solid #e6edf2;
}
figcaption {
  font-size: 9.5pt;
  line-height: 1.45;
  color: var(--muted);
  text-align: center;
  margin: 6px 0 0;
}

table {
  border-collapse: collapse;
  width: 100%;
  margin: 8px 0 14px;
  font-size: 10pt;
  line-height: 1.5;
  table-layout: auto;
  word-break: break-word;
  overflow-wrap: anywhere;
}
thead { display: table-header-group; }
tr { break-inside: avoid; page-break-inside: avoid; }
th, td {
  border: 1px solid var(--thead-border);
  padding: 5px 8px;
  text-align: left;
  vertical-align: top;
}
th {
  background: var(--thead);
  font-weight: 700;
  color: #16344a;
}

code {
  font-family: "Noto Sans Mono CJK TC", "Noto Sans CJK TC", monospace;
  background: #f3f4f6;
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 0.88em;
  word-break: break-all;
}
pre {
  background: #f6f8fa;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 10px 12px;
  overflow: hidden;
  break-inside: avoid;
  page-break-inside: avoid;
  line-height: 1.5;
  font-size: 9pt;
}
pre code { background: none; padding: 0; font-size: 9pt; white-space: pre-wrap; word-break: break-word; }

/* 提示／注意／重點 */
.admonition {
  break-inside: avoid;
  page-break-inside: avoid;
  margin: 10px 0 12px;
  padding: 6px 12px 6px 14px;
  border-left: 5px solid;
  border-radius: 0 6px 6px 0;
}
.admonition .label {
  font-weight: 700;
  font-size: 10.5pt;
  letter-spacing: 0.04em;
  margin: 0 0 2px;
}
.admonition .body { margin: 0; }
.admonition .body p:last-child { margin-bottom: 0; }
.admonition.tip { border-color: #1a7f37; background: #eef8f1; }
.admonition.tip .label { color: #1a7f37; }
.admonition.note { border-color: #0969da; background: #eef5fc; }
.admonition.note .label { color: #0969da; }
.admonition.important { border-color: #8250df; background: #f5f0ff; }
.admonition.important .label { color: #6b3cc9; }
.admonition.warning { border-color: #bc7a00; background: #fff8e8; }
.admonition.warning .label { color: #9a6400; }

hr { display: none; }
a { color: var(--accent); text-decoration: none; }
strong { color: #111; font-weight: 700; }

/* 目錄 */
nav.toc { margin: 4px 0 0; }
nav.toc h2 {
  font-size: 14.5pt;
  break-before: auto;
  page-break-before: auto;
  margin: 4px 0 2px;
  padding-bottom: 2px;
}
.toc-list { list-style: none; padding: 0; margin: 2px 0 0; }
.toc-list li { margin: 0; padding: 0; }
.toc-row {
  display: flex;
  align-items: baseline;
  gap: 0;
  line-height: 1.26;
  margin: 0;
}
.toc-row.l1 { font-size: 10.8pt; }
.toc-row.l2 { padding-left: 1.5em; font-size: 10pt; }
.toc-row a { color: var(--text); flex: 0 1 auto; }
.toc-dots {
  flex: 1 1 auto;
  border-bottom: 1px dotted #8b949e;
  margin: 0 0.55em 0.25em;
  min-width: 12px;
  height: 0;
}
.toc-pg {
  flex: 0 0 2.4em;
  text-align: right;
  font-variant-numeric: tabular-nums;
  color: var(--muted);
}

/* FAQ */
.faq { margin: 0 0 12px; break-inside: avoid; page-break-inside: avoid; }
.faq .q {
  font-weight: 700;
  margin: 0 0 2px;
  color: #16344a;
}
.faq .a { margin: 0 0 0; color: var(--text); }
"""

PRINT_JS = r"""
const fs = require('fs');
const path = require('path');
const { pathToFileURL } = require('url');

async function main() {
  const htmlPath = process.argv[2];
  const pdfPath = process.argv[3];
  const chromePath = process.argv[4];
  const modulesPath = process.argv[5];
  const title = process.argv[6] || '';
  module.paths.unshift(modulesPath);
  const puppeteer = require('puppeteer');
  const browser = await puppeteer.launch({
    executablePath: chromePath,
    headless: true,
    args: ['--no-sandbox', '--disable-gpu', '--font-render-hinting=medium'],
  });
  const page = await browser.newPage();
  await page.goto(pathToFileURL(htmlPath).href, { waitUntil: 'networkidle0', timeout: 120000 });
  const font = "font-family:'Noto Sans CJK TC',sans-serif;font-size:9px;color:#57606a;";
  await page.pdf({
    path: pdfPath,
    format: 'A4',
    printBackground: true,
    displayHeaderFooter: true,
    headerTemplate: `<div style="${font}width:100%;text-align:center;padding:0 18mm 0 18mm;">${title}</div>`,
    footerTemplate: `<div style="${font}width:100%;text-align:center;padding:0 18mm 0 18mm;"><span class="pageNumber"></span> / <span class="totalPages"></span></div>`,
    margin: { top: '16mm', bottom: '16mm', left: '18mm', right: '18mm' },
    preferCSSPageSize: false,
  });
  await browser.close();
}
main().catch((e) => { console.error(e); process.exit(1); });
"""


def wrap_figures(html):
    """把「<p><img></p> + <p><em>圖說</em></p>」收成 figure，圖說置中。"""

    def sub(m):
        alt, src, cap = m.group(1), m.group(2), m.group(3)
        cls = ' class="diagram"' if "diagrams/" in src else ""
        return (
            f"<figure{cls}><img alt=\"{alt}\" src=\"{src}\" />"
            f"<figcaption>{cap}</figcaption></figure>"
        )

    pat = (
        r"<p><img alt=\"([^\"]*)\" src=\"([^\"]+)\"\s*/?></p>\s*"
        r"<p><em>(.*?)</em></p>"
    )
    html, n = re.subn(pat, sub, html, flags=re.S)
    print(f"figures：{n} 組")
    return html


def wrap_admonitions(html):
    """把 blockquote 裡以「提示／注意／重點／說明」開頭的段落變成色塊。"""
    kinds = {
        "提示": "tip",
        "注意": "warning",
        "重點": "important",
        "說明": "note",
        "提醒": "tip",
    }

    def sub(m):
        inner = m.group(1)
        text = re.sub(r"<p>|</p>", "\n", inner)
        text = re.sub(r"<strong>(.*?)</strong>", r"\1", text)
        text = text.strip()
        m2 = re.match(r"^(提示|注意|重點|說明|提醒)[：:]\s*", text)
        if not m2:
            return m.group(0)
        kind = kinds[m2.group(1)]
        label = "提示" if m2.group(1) == "提醒" else m2.group(1)
        body = text[m2.end():].strip()
        # 保留原本 HTML 的粗體等，只去掉開頭標籤
        body_html = re.sub(
            r"^(\s*<p>)?\s*<strong>\s*(?:提示|注意|重點|說明|提醒)[：:]\s*</strong>\s*",
            r"\1",
            inner.strip(),
            count=1,
        )
        return (
            f'<div class="admonition {kind}"><div class="label">{label}</div>'
            f'<div class="body">{body_html}</div></div>'
        )

    html, n = re.subn(r"<blockquote>(.*?)</blockquote>", sub, html, flags=re.S)
    print(f"admonitions：{n} 個")
    return html


def wrap_faq(html):
    """把連續的 <p><strong>Q…</strong></p><p>答案</p> 收成 FAQ 區塊。"""

    def sub(m):
        q, a = m.group(1), m.group(2)
        return f'<div class="faq"><p class="q">{q}</p><p class="a">{a}</p></div>'

    html, n = re.subn(
        r"<p><strong>(Q\d+\..*?)</strong></p>\s*<p>(.*?)</p>",
        sub,
        html,
        flags=re.S,
    )
    print(f"faq：{n} 則")
    return html


def mark_chapter_headings(html):
    """章節 h2（1. … 8. …）換頁；目錄的 h2 不加。"""

    def sub2(m):
        attrs, title = m.group(1), m.group(2)
        if re.match(r"^\d+\.\s", title) and "class=" not in attrs:
            if attrs.endswith(" "):
                attrs = attrs + 'class="chapter" '
            else:
                attrs = attrs + ' class="chapter"'
        return f"<h2{attrs}>{title}</h2>"

    return re.sub(r"<h2([^>]*)>(.*?)</h2>", sub2, html, flags=re.S)


def collect_headings(html):
    """章節與小節（h2.chapter、h3），供目錄與頁碼對照。"""
    items = []
    for m in re.finditer(r"<h([23])([^>]*)>(.*?)</h\1>", html, flags=re.S):
        level = int(m.group(1))
        attrs = m.group(2)
        title = re.sub(r"<[^>]+>", "", m.group(3)).strip()
        if level == 2 and not re.match(r"^\d+\.\s", title):
            continue
        idm = re.search(r'id="([^"]+)"', attrs)
        hid = idm.group(1) if idm else slugify(title)
        items.append({"level": level, "id": hid, "title": title, "page": "00"})
    return items


def build_toc_html(items):
    rows = []
    for it in items:
        lvl = "l1" if it["level"] == 2 else "l2"
        rows.append(
            f'<li><div class="toc-row {lvl}">'
            f'<a href="#{it["id"]}">{it["title"]}</a>'
            f'<span class="toc-dots"></span>'
            f'<span class="toc-pg" data-target="{it["id"]}">{it["page"]}</span>'
            f"</div></li>"
        )
    return (
        '<nav class="toc"><h2 id="toc">目錄</h2>'
        f'<ul class="toc-list">{"".join(rows)}</ul></nav>'
    )


def insert_toc(html, toc_html):
    # 放在第一個章節 h2 之前（保留書名與前言），不要插在 </h1> 後面。
    m = re.search(r'<h2\b[^>]*class="chapter"', html)
    if not m:
        raise SystemExit("找不到章節標題（h2.chapter），無法插入目錄")
    return html[: m.start()] + toc_html + html[m.start() :]


def update_toc_pages(html, pages_by_id):
    def sub(m):
        tid = m.group(1)
        pg = pages_by_id.get(tid, "00")
        return f'<span class="toc-pg" data-target="{tid}">{pg}</span>' if isinstance(pg, int) else (
            f'<span class="toc-pg" data-target="{tid}">{pg}</span>'
        )

    return re.sub(r'<span class="toc-pg" data-target="([^"]+)">[^<]*</span>', sub, html)


def pdf_page_count(pdf):
    out = subprocess.check_output(["pdfinfo", pdf], text=True, stderr=subprocess.DEVNULL)
    m = re.search(r"Pages:\s+(\d+)", out)
    return int(m.group(1)) if m else 0


def pdf_page_texts(pdf):
    n = pdf_page_count(pdf)
    texts = []
    for i in range(1, n + 1):
        t = subprocess.check_output(
            ["pdftotext", "-layout", "-f", str(i), "-l", str(i), pdf, "-"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        texts.append(t)
    return texts


def _norm(s):
    s = s.replace("\u3000", " ").replace("／", "/").replace("－", "-")
    s = re.sub(r"\s+", "", s)
    return s


def heading_pages(pdf, items, header=TITLE):
    """對每個標題找出目錄之後第一次出現的頁碼。"""
    raw_pages = pdf_page_texts(pdf)

    def content_lines(raw):
        lines = []
        for ln in raw.splitlines():
            t = ln.strip()
            if not t:
                continue
            if t == header:
                continue
            if re.match(r"^\d+\s*/\s*\d+$", t):
                continue
            lines.append(t)
        return lines

    start = 1
    for i, raw in enumerate(raw_pages, 1):
        lines = content_lines(raw)
        if lines and lines[0].startswith("1. 先看這裡"):
            start = i
            break

    texts = [_norm(t.replace(header, "")) for t in raw_pages]
    pages = {}
    for it in items:
        needle = _norm(it["title"])
        found = None
        for i, t in enumerate(texts, 1):
            if i < start:
                continue
            if needle in t:
                found = i
                break
        pages[it["id"]] = found if found is not None else 0
    return pages, start, len(texts)


def print_pdf(html_path, pdf_path, chrome):
    js = os.path.join(BUILD, "print_pdf.js")
    open(js, "w", encoding="utf-8").write(PRINT_JS)
    modules = find_puppeteer_root()
    r = subprocess.run(
        ["node", js, html_path, pdf_path, chrome, modules, TITLE],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0 or not os.path.exists(pdf_path):
        sys.exit(f"Puppeteer 列印失敗：\n{r.stdout}\n{r.stderr[-800:]}")


def main():
    import markdown

    os.makedirs(BUILD, exist_ok=True)
    md = open(SRC, encoding="utf-8").read()
    md = TOC_RE.sub("\n\n", md, count=1)
    md = re.sub(r"^---\s*$", "", md, flags=re.M)
    md = render_mermaid(md)
    md = re.sub(r"^(\s*)- \[ \] ", r"\1- ☐ ", md, flags=re.M)
    md = md.replace("](img/", "](../img/")

    html_body = markdown.markdown(
        md,
        extensions=["tables", "fenced_code", "sane_lists", "toc"],
        extension_configs={"toc": {"slugify": lambda v, s: slugify(v, s)}},
    )
    html_body = wrap_figures(html_body)
    html_body = wrap_admonitions(html_body)
    html_body = wrap_faq(html_body)
    html_body = mark_chapter_headings(html_body)

    items = collect_headings(html_body)
    html_body = insert_toc(html_body, build_toc_html(items))

    def write_html(body):
        html = (
            "<!doctype html><html lang='zh-TW'><head><meta charset='utf-8'>"
            f"<title>{TITLE}</title><style>{CSS}</style></head>"
            f"<body>{body}</body></html>"
        )
        hp = os.path.join(BUILD, "manual.html")
        open(hp, "w", encoding="utf-8").write(html)
        return hp

    hp = write_html(html_body)
    chrome = find_chrome()
    tmp_pdf = os.path.join(BUILD, "manual.pdf")

    pages_prev = None
    for pass_n in range(1, 4):
        print_pdf(hp, tmp_pdf, chrome)
        pages, start, n_pages = heading_pages(tmp_pdf, items)
        missing = [it["title"] for it in items if not pages.get(it["id"])]
        print(f"pass {pass_n}：{n_pages} 頁，內容自第 {start} 頁，未對到 {len(missing)} 條")
        if missing:
            print("  未對到：", "；".join(missing[:8]))
        if pages == pages_prev:
            break
        pages_prev = dict(pages)
        html_body = update_toc_pages(html_body, pages)
        hp = write_html(html_body)

    shutil.copyfile(tmp_pdf, OUT)
    print("PDF：", OUT, os.path.getsize(OUT) // 1024, "KB，共", pdf_page_count(OUT), "頁")


if __name__ == "__main__":
    main()
