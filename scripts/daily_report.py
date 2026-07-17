# -*- coding: utf-8 -*-
"""
每日「今日訊號」生成器
================================================================
抓證交所最新收盤 → 跑 12 隻機器人 + KD/RSI 掃描 → 驗證前一交易日的預測 →
產出 web/today.html（發佈到 GitHub Pages 的「今日訊號」頁）。

純標準庫（urllib）+ 已驗證的回測引擎，不需要任何 pip 套件。
由 .github/workflows/daily-report.yml 每個交易日下午自動執行。

紅線：只出參考訊號，非投資建議，不代表未來，投資有風險。
"""
import os, sys, time, json, html, datetime, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "engine"))
from bot_engine import run_bot, kd, rsi          # 已驗證引擎（純標準庫）
from bot_library import BOT_LIBRARY

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept": "application/json, text/plain, */*"}
STOCK_DAY = "https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date={d}&stockNo={s}"

# 掃描的權值股籃子（代號, 名稱）
BASKET = [("2330", "台積電"), ("2317", "鴻海"), ("2454", "聯發科"), ("2308", "台達電"),
          ("2603", "長榮"), ("2882", "國泰金"), ("2412", "中華電"), ("0050", "元大台灣50")]
FLAGSHIP = "2330"

# 預測規則（鎖定，不事後改）：過熱→看空、超賣→看多、其餘中性
def predict(k, r):
    if k is None or r is None:
        return "中性"
    if k >= 75 or r >= 68:
        return "看空"
    if k <= 25 or r <= 32:
        return "看多"
    return "中性"


def _f(x):
    try:
        return float(str(x).replace(",", "").strip())
    except (ValueError, AttributeError, TypeError):
        return None


def fetch_month_rows(stock, y, m):
    url = STOCK_DAY.format(d=f"{y}{m:02d}01", s=stock)
    try:
        req = urllib.request.Request(url, headers=UA)
        data = json.loads(urllib.request.urlopen(req, timeout=25).read().decode("utf-8"))
    except Exception as e:
        print(f"  [warn] {stock} {y}{m:02d}: {e}", flush=True)
        return []
    if data.get("stat") == "OK" and data.get("data"):
        return data["data"]
    return []


def fetch_series(stock, months=5):
    """回傳 [(民國日期, close), ...] 由舊到新。"""
    rows = []
    today = datetime.date.today()
    for back in range(months - 1, -1, -1):
        y, m = today.year, today.month - back
        while m <= 0:
            m += 12
            y -= 1
        rows.extend(fetch_month_rows(stock, y, m))
        time.sleep(2)   # 證交所限流保護
    out = []
    for r in rows:
        c = _f(r[6]) if len(r) > 6 else None
        if c is not None:
            out.append((r[0], c))
    return out


def roc_to_ad(roc_date):
    """民國 '115/06/26' -> '2026/06/26'"""
    try:
        y, m, d = roc_date.split("/")
        return f"{int(y) + 1911}/{m}/{d}"
    except Exception:
        return roc_date


def scan():
    series = {}
    for code, _ in BASKET:
        series[code] = fetch_series(code, 5)
    last_date = None
    rows = []
    for code, name in BASKET:
        s = series.get(code, [])
        closes = [c for _, c in s]
        if len(closes) < 60:
            print(f"  [skip] {code} 資料不足 {len(closes)}", flush=True)
            continue
        last_date = s[-1][0]
        k_today = kd(closes)["k"][-1]
        r_today = rsi(closes)[-1]
        # 昨日狀態（用到昨日為止的序列）→ 昨日預測 → 用今日實際驗證
        k_prev = kd(closes[:-1])["k"][-1] if len(closes) > 1 else None
        r_prev = rsi(closes[:-1])[-1] if len(closes) > 1 else None
        pred_yest = predict(k_prev, r_prev)
        chg_today = (closes[-1] / closes[-2] - 1) * 100 if len(closes) > 1 else 0.0
        verified = None
        if pred_yest == "看空":
            verified = chg_today < 0
        elif pred_yest == "看多":
            verified = chg_today > 0
        rows.append({
            "code": code, "name": name, "close": closes[-1],
            "chg": round(chg_today, 2),
            "kd": round(k_today, 0) if k_today is not None else None,
            "rsi": round(r_today, 0) if r_today is not None else None,
            "pred_tomorrow": predict(k_today, r_today),
            "pred_yest": pred_yest, "verified": verified,
        })
    # 旗艦股 12 機器人今日訊號
    flag = [c for c in [cc for cc, _ in BASKET] if c == FLAGSHIP]
    bot_signals = []
    fs = series.get(FLAGSHIP, [])
    fcloses = [c for _, c in fs]
    if len(fcloses) >= 60:
        for b in BOT_LIBRARY:
            res = run_bot(b, fcloses, fee_pct=0.585)
            bot_signals.append((b["name"], res.final_signal))
    return rows, bot_signals, last_date


# ── HTML 產生 ──────────────────────────────────────────────
SIG_COLOR = {"買進": "#3ddc97", "賣出": "#ff6b6b", "續抱": "#ffb454", "觀望": "#8b97a8"}
SIG_ICON = {"買進": "🟢", "賣出": "🔴", "續抱": "🟡", "觀望": "⚪"}


def esc(x):
    return html.escape(str(x))


def build_html(rows, bot_signals, last_date, generated_utc):
    ad = roc_to_ad(last_date) if last_date else "—"
    # 昨日驗證命中率
    checked = [r for r in rows if r["verified"] is not None]
    hits = sum(1 for r in checked if r["verified"])
    rate = round(hits / len(checked) * 100) if checked else 0

    def scan_row(r):
        pc = "#ff6b6b" if r["chg"] < 0 else ("#3ddc97" if r["chg"] > 0 else "#8b97a8")
        pr = r["pred_tomorrow"]
        prc = "#ff6b6b" if pr == "看空" else ("#3ddc97" if pr == "看多" else "#8b97a8")
        st = "過熱" if (r["kd"] and r["kd"] >= 75) or (r["rsi"] and r["rsi"] >= 68) else \
             ("超賣" if (r["kd"] is not None and r["kd"] <= 25) or (r["rsi"] is not None and r["rsi"] <= 32) else "中性")
        return (f"<tr><td>{esc(r['code'])} {esc(r['name'])}</td>"
                f"<td class='r'>{r['close']:g}</td>"
                f"<td class='r' style='color:{pc}'>{r['chg']:+.2f}%</td>"
                f"<td class='c'>{'' if r['kd'] is None else int(r['kd'])}</td>"
                f"<td class='c'>{'' if r['rsi'] is None else int(r['rsi'])}</td>"
                f"<td class='c'>{st}</td>"
                f"<td class='c' style='color:{prc};font-weight:700'>{pr}</td></tr>")

    def verify_row(r):
        if r["verified"] is None:
            return ""
        prc = "#ff6b6b" if r["pred_yest"] == "看空" else "#3ddc97"
        cc = "#ff6b6b" if r["chg"] < 0 else "#3ddc97"
        ok = ("<span style='color:#3ddc97'>✅ 命中</span>" if r["verified"]
              else "<span style='color:#8b97a8'>✗ 未中</span>")
        return (f"<tr><td>{esc(r['code'])} {esc(r['name'])}</td>"
                f"<td class='c' style='color:{prc};font-weight:700'>{esc(r['pred_yest'])}</td>"
                f"<td class='r' style='color:{cc}'>{r['chg']:+.2f}%</td>"
                f"<td class='c'>{ok}</td></tr>")

    scan_html = "".join(scan_row(r) for r in rows)
    verify_html = "".join(verify_row(r) for r in rows if r["verified"] is not None) \
        or "<tr><td colspan='4' class='c' style='color:#8b97a8'>無可驗證的方向性預測（前一日皆中性）</td></tr>"
    bots_cells = ""
    for i in range(0, len(bot_signals), 2):
        left = bot_signals[i]
        right = bot_signals[i + 1] if i + 1 < len(bot_signals) else ("", "")
        def cell(bs):
            if not bs[0]:
                return "<td></td><td></td>"
            col = SIG_COLOR.get(bs[1], "#8b97a8"); ic = SIG_ICON.get(bs[1], "")
            return f"<td>{esc(bs[0])}</td><td class='c' style='color:{col}'>{ic} {esc(bs[1])}</td>"
        bots_cells += f"<tr>{cell(left)}{cell(right)}</tr>"

    return f"""<!DOCTYPE html>
<html lang="zh-Hant"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>今日訊號 · {esc(ad)} — 林博股票機器人工廠</title>
<style>
  :root{{--bg:#0e1116;--surface:#171b22;--surface2:#1f2530;--line:#2a3140;--text:#e8edf4;--muted:#8b97a8;--accent:#3ddc97;--accent2:#ffb454;--sell:#ff6b6b;}}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:var(--bg);color:var(--text);font-family:"PingFang TC","Microsoft JhengHei",system-ui,sans-serif;line-height:1.6;padding:0 0 60px}}
  .top{{background:var(--surface);border-bottom:1px solid var(--line);padding:16px 24px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}}
  .logo{{font-weight:700;font-size:18px}} .logo b{{color:var(--accent)}}
  .tag{{font-size:12px;color:var(--muted);border:1px solid var(--line);padding:3px 10px;border-radius:20px}}
  .wrap{{max-width:920px;margin:26px auto;padding:0 20px}}
  h1{{font-size:22px;margin-bottom:4px}} h1 .date{{color:var(--accent)}}
  .sub{{color:var(--muted);font-size:14px;margin-bottom:18px}}
  .disc{{font-size:12px;color:#7a5b00;background:#FFF7E6;border:1px solid #E0A800;border-radius:8px;padding:10px 13px;margin-bottom:20px}}
  h2{{font-size:16px;color:var(--accent);margin:24px 0 10px}}
  .rate{{font-size:15px;font-weight:700;color:var(--accent);margin:8px 0}}
  .wrapt{{overflow-x:auto;border:1px solid var(--line);border-radius:10px}}
  table{{width:100%;border-collapse:collapse;font-size:13.5px;min-width:520px}}
  th{{background:var(--surface2);color:var(--muted);font-size:12px;font-weight:600;padding:9px 11px;text-align:left;white-space:nowrap}}
  td{{padding:9px 11px;border-top:1px solid var(--line);white-space:nowrap}}
  td.c{{text-align:center}} td.r{{text-align:right;font-family:monospace}}
  tbody tr:hover{{background:var(--surface2)}}
  .note{{font-size:12px;color:var(--muted);margin-top:8px}}
  .foot{{text-align:center;color:var(--muted);font-size:12px;margin-top:30px;line-height:1.8}}
  a{{color:var(--accent);text-decoration:none}}
</style></head><body>
<div class="top">
  <div class="logo">林博<b>股票機器人工廠</b></div>
  <span class="tag">今日訊號</span>
  <span class="tag">自動生成</span>
  <span class="tag" style="margin-left:auto"><a href="./index.html">← 回工廠</a></span>
</div>
<div class="wrap">
  <h1>今日訊號 <span class="date">{esc(ad)}</span></h1>
  <p class="sub">依台灣證交所收盤資料，每個交易日下午自動生成。KD-K ≥ 75 或 RSI ≥ 68 記為「看空」、≤ 25 / ≤ 32 記為「看多」。</p>
  <div class="disc">⚠ 本頁為平台依公開歷史技術指標產生的<b>參考訊號</b>，<b>非投資建議、非未來預測</b>。技術指標對單日漲跌僅具統計傾向、不具因果。回測與訊號皆不代表未來，投資有風險，請自行判斷、自負盈虧。平台只出訊號、不代下單、不碰資金。</div>

  <h2>① 今日收盤掃描（明日方向預測）</h2>
  <div class="wrapt"><table>
    <thead><tr><th>股票</th><th style="text-align:right">收盤</th><th style="text-align:right">漲跌</th><th style="text-align:center">KD-K</th><th style="text-align:center">RSI</th><th style="text-align:center">狀態</th><th style="text-align:center">明日預測</th></tr></thead>
    <tbody>{scan_html}</tbody>
  </table></div>
  <p class="note">「明日預測」為隔一交易日的方向傾向；中性者不列入計分。</p>

  <h2>② 昨日預測 vs 今日實際（命中驗證）</h2>
  <div class="rate">昨日方向性預測命中率：{hits} / {len(checked)} = {rate}%</div>
  <div class="wrapt"><table>
    <thead><tr><th>股票</th><th style="text-align:center">昨日預測</th><th style="text-align:right">今日實際</th><th style="text-align:center">結果</th></tr></thead>
    <tbody>{verify_html}</tbody>
  </table></div>
  <p class="note">單日、少樣本，命中率僅供觀察，不具統計意義；大盤整體漲跌會主導單日結果。</p>

  <h2>③ 12 隻機器人對 台積電(2330) 的今日訊號</h2>
  <div class="wrapt"><table>
    <thead><tr><th>機器人</th><th style="text-align:center">訊號</th><th>機器人</th><th style="text-align:center">訊號</th></tr></thead>
    <tbody>{bots_cells}</tbody>
  </table></div>

  <div class="foot">
    資料來源：台灣證交所公開資料（未還原權息）　·　自動生成於 {esc(generated_utc)} UTC<br>
    林博股票機器人工廠　|　本平台提供分析工具，非投資建議。歷史回測與訊號不代表未來，股市投資可能虧損，請自負風險。
  </div>
</div></body></html>"""


def main():
    print("開始生成今日訊號…", flush=True)
    rows, bot_signals, last_date = scan()
    if not rows:
        print("無足夠資料，跳過生成（可能是非交易日或抓取失敗）。", flush=True)
        return 0
    generated = datetime.datetime.now(datetime.timezone.utc).strftime("%Y/%m/%d %H:%M")
    out_html = build_html(rows, bot_signals, last_date, generated)
    out_path = os.path.join(HERE, "..", "web", "today.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(out_html)
    print(f"已生成 {out_path}（資料日 {roc_to_ad(last_date)}，{len(rows)} 檔）", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
