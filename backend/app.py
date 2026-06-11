# -*- coding: utf-8 -*-
"""
第一期 MVP 後端 (app.py)
================================================================
提供的 API：
  GET  /                         健康檢查
  GET  /api/health               健康檢查（JSON）
  GET  /api/bots                 回傳 8 個範例機器人（資料庫）
  GET  /api/prices?stock=2330&months=12     抓單檔歷史每日收盤價
  POST /api/backtest             抓股價 + 跑回測，回傳結果

資料來源：台灣證交所個股日成交資訊 STOCK_DAY（公開、免金鑰）。
  端點回傳某檔股票「一整個月」的每日資料，收盤價在每筆 data 的 index 6。
  要回測一年就逐月抓 12 次，再把每月 data 由舊到新串起來。

紅線：本平台只產生「參考訊號」，絕不自動下單、不接券商、不碰金流。

啟動：
  python app.py            # 開發
  gunicorn app:app         # 正式（Render）
"""
import sys, os, time, datetime, re, threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))

from flask import Flask, request, jsonify
from flask_cors import CORS
import requests

from bot_engine import run_bot, DEFAULT_FEE_PCT      # 已驗證的回測引擎
from bot_library import public_library, get_bot       # 8 個範例機器人資料庫

app = Flask(__name__)
CORS(app)   # 讓前端（GitHub Pages）能跨網域呼叫

# 個股「一整月」歷史成交。收盤價在每筆 data 的 index 6。
STOCK_DAY_URL = "https://www.twse.com.tw/exchangeReport/STOCK_DAY"
CLOSE_INDEX = 6   # fields: 日期,成交股數,成交金額,開盤,最高,最低,[收盤=6],漲跌,筆數,(註記)
REQUEST_INTERVAL = 2.0   # 證交所限流：每 5 秒最多 3 次 → 每次間隔 2 秒最安全

# 瀏覽器式 User-Agent，避免證交所擋掉 default python-requests
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
})

# 逐月收盤價快取：key = (stock, "YYYYMM") → list[float]。當月仍在變動，不快取「本月」。
_CACHE = {}
_CACHE_LOCK = threading.Lock()
_last_request_ts = [0.0]
_STOCK_RE = re.compile(r"^[0-9A-Za-z]{4,6}$")   # 台股代號：4 碼數字為主，ETF/權證可能含字母


def _to_float(s):
    """去逗號轉 float，轉不動回 None。"""
    try:
        return float(str(s).replace(",", "").strip())
    except (ValueError, AttributeError, TypeError):
        return None


def _throttle():
    """確保兩次對證交所的 request 至少間隔 REQUEST_INTERVAL 秒。"""
    now = time.time()
    wait = REQUEST_INTERVAL - (now - _last_request_ts[0])
    if wait > 0:
        time.sleep(wait)
    _last_request_ts[0] = time.time()


def _fetch_month(stock, y, m):
    """抓某檔股票某個月的每日收盤價（由舊到新）。會用快取，且尊重限流。"""
    key = (stock, f"{y}{m:02d}")
    today = datetime.date.today()
    is_current_month = (y == today.year and m == today.month)

    if not is_current_month:           # 過去月份資料固定，可永久快取
        with _CACHE_LOCK:
            if key in _CACHE:
                return list(_CACHE[key])

    date_str = f"{y}{m:02d}01"
    url = f"{STOCK_DAY_URL}?response=json&date={date_str}&stockNo={stock}"
    closes = []
    try:
        _throttle()
        r = SESSION.get(url, timeout=20)
        data = r.json()
    except Exception as e:
        print(f"  [警告] 抓 {stock} {date_str} 失敗：{e}")
        return closes

    if data.get("stat") == "OK" and data.get("data"):
        for row in data["data"]:
            if len(row) > CLOSE_INDEX:
                c = _to_float(row[CLOSE_INDEX])
                if c is not None:
                    closes.append(c)
    else:
        print(f"  [注意] {stock} {date_str} 無資料或 stat={data.get('stat')}")

    if not is_current_month and closes:
        with _CACHE_LOCK:
            _CACHE[key] = list(closes)
    return closes


def fetch_prices(stock, months=12):
    """
    抓某檔股票最近 months 個月的每日收盤價，回傳由舊到新的 float 陣列。
    """
    months = max(1, min(int(months), 24))   # 上限 24 個月，避免一次打太多
    closes = []
    today = datetime.date.today()
    for back in range(months - 1, -1, -1):
        y, m = today.year, today.month - back
        while m <= 0:
            m += 12
            y -= 1
        closes.extend(_fetch_month(stock, y, m))
    return closes


def _valid_stock(stock):
    return bool(stock and _STOCK_RE.match(stock))


# ── API ───────────────────────────────────────────────────
@app.route("/")
def home():
    return ("股票機器人平台後端 MVP 運作中。"
            "試試 /api/prices?stock=2330&months=3 或 /api/bots")


@app.route("/api/health")
def api_health():
    return jsonify({"status": "ok", "bots": len(public_library())})


@app.route("/api/bots")
def api_bots():
    """回傳 8 個範例機器人（前端工廠的『範例機器人』區直接吃這個）。"""
    return jsonify({"bots": public_library()})


@app.route("/api/prices")
def api_prices():
    stock = request.args.get("stock", "2330").strip()
    if not _valid_stock(stock):
        return jsonify({"error": "股票代號格式不正確（請輸入 4 碼代號，如 2330）"}), 400
    months = request.args.get("months", 12)
    closes = fetch_prices(stock, months)
    return jsonify({
        "stock": stock,
        "days": len(closes),
        "last_close": closes[-1] if closes else None,
        "prices": closes,
    })


@app.route("/api/backtest", methods=["POST"])
def api_backtest():
    body = request.get_json(force=True, silent=True) or {}
    stock = str(body.get("stock", "2330")).strip()
    if not _valid_stock(stock):
        return jsonify({"error": "股票代號格式不正確（請輸入 4 碼代號，如 2330）"}), 400
    months = body.get("months", 12)

    # 手續費：未指定時用台股預設來回成本；可傳 0 看「無成本」理想值
    fee_pct = body.get("fee_pct", DEFAULT_FEE_PCT)
    try:
        fee_pct = max(0.0, float(fee_pct))
    except (ValueError, TypeError):
        fee_pct = DEFAULT_FEE_PCT

    # bot 來源：可直接帶規則 JSON（bot），或指定範例庫 id（bot_id）
    bot = body.get("bot")
    if not bot and body.get("bot_id"):
        bot = get_bot(body["bot_id"])
    if not bot or "buy" not in bot or "sell" not in bot:
        return jsonify({"error": "缺少有效的 bot 規則（需含 buy 與 sell）"}), 400

    closes = fetch_prices(stock, months)
    if len(closes) < 30:
        return jsonify({
            "error": f"資料不足（只抓到 {len(closes)} 天），無法回測。"
                     f"可能是代號錯誤、停牌或新上市。"
        }), 400

    r = run_bot(bot, closes, fee_pct=fee_pct)
    return jsonify({
        "stock": stock,
        "days": len(closes),
        "last_close": closes[-1],
        "fee_pct": r.fee_pct,
        "trades": r.trades,
        "win_rate": r.win_rate,
        "total_return_pct": r.total_return_pct,
        "compound_return_pct": r.compound_return_pct,
        "max_drawdown_pct": r.max_drawdown_pct,
        "buy_hold_return_pct": r.buy_hold_return_pct,
        "avg_return_pct": r.avg_return_pct,
        "beats_buy_hold": r.beats_buy_hold,
        "final_signal": r.final_signal,
        "log": r.log,
        "prices": closes,
        "markers": r.markers,
        "disclaimer": "本結果為歷史回測，已扣交易成本，仍非投資建議，"
                      "不代表未來，投資有風險。",
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
