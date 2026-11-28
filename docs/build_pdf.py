# -*- coding: utf-8 -*-
"""把 docs/操作手冊.md 轉成 docs/操作手冊.pdf（圖、表、中文字型都嵌入）。

流程：mermaid 區塊 → mmdc 轉 PNG → python-markdown 轉 HTML → Chrome 無頭列印成 PDF。
需要：google-chrome（或設環境變數 CHROME）、mermaid-cli（mmdc；或設環境變數 MMDC）、python 套件 markdown、
      字型 Noto Sans CJK TC。
用法：python3 docs/build_pdf.py
"""
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


def _which(*names):
    for n in names:
        p = os.environ.get(n.upper()) if n.isalpha() else None
        if p and os.path.exists(p):
            return p
    return None


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
    for c in ("/workspace/mmdc/node_modules/.bin/mmdc", os.path.expanduser("~/node_modules/.bin/mmdc")):
        if os.path.exists(c):
            return c
    sys.exit("找不到 mmdc（npm i @mermaid-js/mermaid-cli，或設環境變數 MMDC）")


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
    json.dump({"theme": "default", "themeVariables": {"fontFamily": "Noto Sans CJK TC, sans-serif", "fontSize": "16px"},
               "flowchart": {"htmlLabels": True, "useMaxWidth": True}}, open(mcfg, "w"))
    n = [0]

    def sub(m):
        n[0] += 1
        mmd = os.path.join(BUILD, "diagrams", f"d{n[0]}.mmd")
        png = os.path.join(BUILD, "diagrams", f"d{n[0]}.png")
        open(mmd, "w", encoding="utf-8").write(m.group(1))
        r = subprocess.run([mmdc, "-p", pcfg, "-c", mcfg, "-i", mmd, "-o", png, "-b", "white", "-s", "2"],
                           capture_output=True, text=True)
        if r.returncode != 0 or not os.path.exists(png):
            sys.exit(f"mermaid 第 {n[0]} 張轉換失敗：\n{r.stdout}\n{r.stderr}")
        return f"![流程圖](diagrams/d{n[0]}.png)"

    out = re.sub(r"```mermaid\n(.*?)```", sub, md, flags=re.S)
    print(f"mermaid：{n[0]} 張")
    return out


CSS = """
@page { size: A4; margin: 14mm 12mm 16mm 12mm; }
html { font-family: 'Noto Sans CJK TC', 'Noto Sans CJK SC', sans-serif; font-size: 10.5pt; color: #1f2328; }
body { line-height: 1.65; }
h1 { font-size: 22pt; border-bottom: 3px solid #0e9bd8; padding-bottom: 6px; margin-top: 0; }
h2 { font-size: 16pt; border-bottom: 2px solid #d0d7de; padding-bottom: 4px; margin-top: 26px; page-break-after: avoid; }
h3 { font-size: 13pt; color: #0b6aa2; margin-top: 20px; page-break-after: avoid; }
h2, h3 { break-after: avoid; }
p, li { orphans: 3; widows: 3; }
img { max-width: 100%; height: auto; border: 1px solid #d0d7de; border-radius: 4px; display: block; margin: 8px auto; }
p:has(> img) { text-align: center; break-inside: avoid; margin: 6px 0 2px; break-after: avoid; }
p:has(> em:only-child) { text-align: center; color: #57606a; font-size: 9.2pt; margin: 0 0 12px; }
table { border-collapse: collapse; width: 100%; margin: 8px 0 12px; font-size: 9.6pt; break-inside: auto; }
tr { break-inside: avoid; }
th, td { border: 1px solid #c9d1d9; padding: 4px 8px; text-align: left; vertical-align: top; }
th { background: #eaf4fb; }
code { font-family: 'Noto Sans Mono CJK TC', 'Noto Sans Mono CJK SC', monospace; background: #f3f4f6; padding: 1px 4px; border-radius: 3px; font-size: 9.4pt; }
pre { background: #f6f8fa; border: 1px solid #d0d7de; border-radius: 6px; padding: 10px 12px; overflow: hidden; break-inside: avoid; }
pre code { background: none; padding: 0; font-size: 8.8pt; white-space: pre-wrap; }
blockquote { margin: 10px 0; padding: 6px 14px; border-left: 5px solid #f0a020; background: #fff8e8; color: #4a3b10; break-inside: avoid; }
blockquote p { margin: 4px 0; }
hr { border: none; border-top: 1px solid #d0d7de; margin: 18px 0; }
a { color: #0b6aa2; text-decoration: none; }
strong { color: #111; }
"""


def main():
    import markdown
    md = open(SRC, encoding="utf-8").read()
    os.makedirs(BUILD, exist_ok=True)
    md = render_mermaid(md)
    md = re.sub(r"^(\s*)- \[ \] ", r"\1- ☐ ", md, flags=re.M)
    md = md.replace("](img/", "](../img/")
    html_body = markdown.markdown(md, extensions=["tables", "fenced_code", "sane_lists", "toc"],
                                  extension_configs={"toc": {"slugify": lambda v, s: slugify(v, s)}})
    html = ("<!doctype html><html lang='zh-TW'><head><meta charset='utf-8'><title>ScopeWaveSaver 功能介面操作手冊</title>"
            f"<style>{CSS}</style></head><body>{html_body}</body></html>")
    hp = os.path.join(BUILD, "manual.html")
    open(hp, "w", encoding="utf-8").write(html)
    chrome = find_chrome()
    r = subprocess.run([chrome, "--headless=new", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
                        "--run-all-compositor-stages-before-draw", "--virtual-time-budget=20000",
                        f"--print-to-pdf={OUT}", "file://" + hp], capture_output=True, text=True)
    if not os.path.exists(OUT):
        sys.exit(f"Chrome 列印失敗：{r.stderr[-400:]}")
    print("PDF：", OUT, os.path.getsize(OUT) // 1024, "KB")


if __name__ == "__main__":
    main()
