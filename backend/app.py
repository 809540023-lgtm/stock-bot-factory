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
import base64, hashlib, hmac
import sys, os, time, datetime, re, threading
from html import escape
from secrets import compare_digest
from uuid import UUID

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))

from flask import Flask, request, jsonify
from flask_cors import CORS
import requests

from bot_engine import run_bot, DEFAULT_FEE_PCT      # 已驗證的回測引擎
from bot_library import public_library, get_bot       # 8 個範例機器人資料庫
from investment_plans.schemas import InvestmentPlanRequest, LineSubscriptionRequest, PlanReviewRequest
from investment_plans.service import InvestmentPlanStore

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
PLAN_STORE = InvestmentPlanStore(os.environ.get("INVESTMENT_PLAN_JSON_PATH", "data/investment_plans_store.json"))
LINE_BINDING_RE = re.compile(r"^(?:綁定|绑定|bind|subscribe|訂閱)\s*[:：]?\s*(?P<user_id>[0-9A-Za-z_.@-]{1,80})$", re.IGNORECASE)


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


def _verify_line_signature(raw_body: bytes, signature: str | None) -> bool:
    channel_secret = os.environ.get("LINE_CHANNEL_SECRET", "")
    if not channel_secret or not signature:
        return False
    digest = hmac.new(channel_secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return compare_digest(expected, signature)


def _line_reply(reply_token: str | None, text: str) -> bool:
    access_token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    if not access_token or not reply_token:
        return False
    try:
        resp = requests.post(
            "https://api.line.me/v2/bot/message/reply",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json={"replyToken": reply_token, "messages": [{"type": "text", "text": text[:5000]}]},
            timeout=10,
        )
        if resp.status_code >= 300:
            print(f"[LINE] reply failed: {resp.status_code} {resp.text[:300]}")
            return False
        return True
    except Exception as exc:
        print(f"[LINE] reply exception: {exc}")
        return False


def _bind_line_user(line_user_id: str, member_id: str, frequency: str = "daily"):
    return PLAN_STORE.create_line_subscription(LineSubscriptionRequest(
        user_id=member_id,
        line_user_id=line_user_id,
        frequency=frequency,
        consent=True,
    ))


def _line_binding_help() -> str:
    return (
        "歡迎加入 2408 每日投資更新。\n"
        "請回覆：綁定 你的會員ID\n"
        "例如：綁定 guest\n"
        "完成後，系統會把這個 LINE 帳號綁定到你的投資計畫訂閱。"
    )


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


# ── 投資計畫與 LINE 訂閱 MVP ────────────────────────────────
def _money(value):
    if value is None:
        return "-"
    return f"NT$ {float(value):,.0f}"


def _split_csv(value):
    if not value:
        return []
    return [item.strip() for item in str(value).replace("，", ",").split(",") if item.strip()]


def _parse_uuid(value):
    if not value:
        return None
    return UUID(str(value))


def _plan_shell(title, body):
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{escape(title)}</title>
  <style>
    :root {{ --bg:#f7f8f2; --panel:#fff; --ink:#17201b; --muted:#5d6b63; --line:#d9dfd7; --accent:#25665b; --accent2:#9b4f2f; --soft:#eef3eb; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:"PingFang TC",system-ui,sans-serif; background:var(--bg); color:var(--ink); }}
    .wrap {{ max-width:1120px; margin:0 auto; padding:28px 20px 64px; }}
    .topbar {{ display:flex; justify-content:space-between; gap:14px; align-items:center; margin-bottom:18px; }}
    .brand {{ color:var(--accent); font-weight:800; text-decoration:none; }}
    .hero,.section {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:22px; }}
    .section {{ margin-top:16px; }}
    .eyebrow {{ color:var(--accent2); font-size:12px; font-weight:800; letter-spacing:.08em; text-transform:uppercase; }}
    h1 {{ margin:8px 0 10px; font-size:36px; line-height:1.12; }}
    h2 {{ margin:0 0 12px; font-size:22px; }}
    h3 {{ margin:0 0 8px; font-size:18px; }}
    p,li {{ color:var(--muted); line-height:1.7; }}
    .grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; }}
    .grid.three {{ grid-template-columns:repeat(3,minmax(0,1fr)); }}
    .card {{ border:1px solid var(--line); border-radius:8px; padding:16px; background:#fff; }}
    .callout,.metric {{ background:var(--soft); border-radius:8px; padding:16px; }}
    .label {{ color:var(--muted); font-size:13px; }}
    .value {{ margin-top:6px; font-size:24px; font-weight:800; }}
    .actions {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:16px; }}
    .btn,button {{ border:0; border-radius:8px; background:var(--accent); color:#fff; padding:11px 14px; font:inherit; font-weight:800; text-decoration:none; cursor:pointer; }}
    .btn.alt {{ background:var(--accent2); }}
    form.stack {{ display:grid; gap:12px; }}
    label {{ display:grid; gap:6px; color:var(--muted); font-size:14px; }}
    input,select,textarea {{ width:100%; border:1px solid var(--line); border-radius:8px; padding:11px 12px; font:inherit; background:#fff; color:var(--ink); }}
    textarea {{ min-height:84px; resize:vertical; }}
    @media(max-width:820px) {{ h1 {{ font-size:30px; }} .grid,.grid.three {{ grid-template-columns:1fr; }} .topbar {{ align-items:flex-start; flex-direction:column; }} }}
  </style>
</head>
<body><div class="wrap">
  <div class="topbar">
    <a class="brand" href="/investment-plans">AI 單股投資計畫</a>
    <a class="btn alt" href="/investment-plans/new">建立計畫</a>
  </div>
  {body}
</div></body></html>"""


@app.route("/investment-plans")
def investment_plan_home():
    user_id = request.args.get("user_id")
    plans = PLAN_STORE.list_for_user(user_id)
    cards = "".join(
        "<article class='card'>"
        f"<div class='eyebrow'>{escape(plan.request.plan_type)}</div>"
        f"<h3>{escape(plan.title)}</h3>"
        f"<p>{escape(plan.summary)}</p>"
        f"<div class='actions'><a class='btn' href='/investment-plans/{plan.id}'>查看計畫</a></div>"
        "</article>"
        for plan in plans[:12]
    ) or "<p>目前還沒有投資計畫。</p>"
    body = f"""
    <section class="hero">
      <div class="eyebrow">Single Stock Planning</div>
      <h1>完整分析 + 每月零存整付</h1>
      <p>會員可以針對單一股票建立完整分析計畫，也可以輸入每月 3,000 元、5,000 元或自訂金額，取得可追蹤的投入規則。</p>
      <div class="actions">
        <a class="btn" href="/investment-plans/new?plan_type=recurring_investment">零存整付</a>
        <a class="btn alt" href="/investment-plans/new?plan_type=full_analysis">完整分析</a>
        <a class="btn" href="/investment-plans/line-subscribe">LINE 每日推播</a>
      </div>
    </section>
    <section class="section"><h2>已建立計畫</h2><div class="grid">{cards}</div></section>
    """
    return _plan_shell("AI 單股投資計畫", body)


@app.route("/investment-plans/new")
def investment_plan_new():
    plan_type = request.args.get("plan_type", "recurring_investment")
    recurring_selected = "selected" if plan_type == "recurring_investment" else ""
    full_selected = "selected" if plan_type == "full_analysis" else ""
    body = f"""
    <section class="hero">
      <div class="eyebrow">Create Plan</div>
      <h1>建立會員單股投資計畫</h1>
      <p>輸入股票、月存金額、風險偏好、原料與公開事件追蹤關鍵字，產生可每月更新的投資紀律。</p>
    </section>
    <section class="section">
      <form class="stack" method="post" action="/investment-plans/create">
        <div class="grid">
          <label>會員 ID<input name="user_id" value="guest" required /></label>
          <label>計畫類型<select name="plan_type"><option value="recurring_investment" {recurring_selected}>零存整付投資計畫</option><option value="full_analysis" {full_selected}>完整股票分析計畫</option></select></label>
          <label>股票代號<input name="stock_symbol" value="2408" required /></label>
          <label>股票名稱<input name="stock_name" value="南亞科" /></label>
          <label>目前股價<input type="number" step="0.01" name="current_price" value="324" required /></label>
          <label>每月投入金額<input type="number" step="1" name="monthly_amount" value="3000" /></label>
          <label>初始投入金額<input type="number" step="1" name="initial_amount" value="0" /></label>
          <label>投資年限<input type="number" step="1" name="investment_years" value="5" /></label>
          <label>風險偏好<select name="risk_profile"><option value="conservative">保守</option><option value="balanced" selected>穩健</option><option value="aggressive">積極</option></select></label>
          <label>產業屬性<select name="industry_cycle"><option value="unknown">未知</option><option value="stable">穩定型</option><option value="cyclical" selected>景氣循環型</option><option value="growth">成長型</option></select></label>
          <label>估值狀態<select name="valuation_level"><option value="unknown">未知</option><option value="undervalued">偏低</option><option value="fair">合理</option><option value="expensive" selected>偏高</option><option value="overheated">過熱</option></select></label>
          <label>最大可承受虧損 %<input type="number" step="1" name="max_loss_percent" value="25" /></label>
          <label>目標報酬 %<input type="number" step="1" name="target_return_percent" value="50" /></label>
          <label>目前平均成本<input type="number" step="0.01" name="average_cost" /></label>
          <label>目前持有股數<input type="number" step="0.0001" name="shares_owned" value="0" /></label>
        </div>
        <label>原料/成本追蹤<textarea name="tracked_materials">DRAM, DDR4, DDR5, 矽晶圓, 光阻, 特用氣體, 封裝材料</textarea></label>
        <label>公開事件追蹤關鍵字<textarea name="public_event_keywords">台塑 日本 行程, 南亞科 日本 客戶, 台塑集團 日本 投資, DRAM 日本 供應鏈</textarea></label>
        <button type="submit">產生投資計畫</button>
      </form>
    </section>
    """
    return _plan_shell("建立投資計畫", body)


@app.route("/investment-plans/create", methods=["POST"])
def investment_plan_create():
    try:
        monthly_amount = request.form.get("monthly_amount")
        average_cost = request.form.get("average_cost")
        plan = PLAN_STORE.create(InvestmentPlanRequest(
            user_id=request.form.get("user_id", "guest"),
            stock_symbol=request.form["stock_symbol"],
            stock_name=request.form.get("stock_name") or None,
            plan_type=request.form["plan_type"],
            current_price=float(request.form["current_price"]),
            monthly_amount=float(monthly_amount) if monthly_amount else None,
            initial_amount=float(request.form.get("initial_amount") or 0),
            investment_years=int(request.form.get("investment_years") or 5),
            risk_profile=request.form.get("risk_profile", "balanced"),
            max_loss_percent=float(request.form.get("max_loss_percent") or 25),
            target_return_percent=float(request.form.get("target_return_percent") or 50),
            average_cost=float(average_cost) if average_cost else None,
            shares_owned=float(request.form.get("shares_owned") or 0),
            industry_cycle=request.form.get("industry_cycle", "unknown"),
            valuation_level=request.form.get("valuation_level", "unknown"),
            tracked_materials=_split_csv(request.form.get("tracked_materials")),
            public_event_keywords=_split_csv(request.form.get("public_event_keywords")),
        ))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 422
    return ("", 303, {"Location": f"/investment-plans/{plan.id}"})


@app.route("/investment-plans/line-subscribe")
def investment_line_subscribe():
    line_url = os.environ.get("LINE_OFFICIAL_ACCOUNT_URL", "")
    webhook_url = os.environ.get("LINE_WEBHOOK_URL", "https://stock-bot-backend-kcbc.onrender.com/investment-plans/line-webhook")
    action = f"<a class='btn' href='{escape(line_url)}'>加入 LINE 每日推播</a>" if line_url else "<p>尚未設定 LINE_OFFICIAL_ACCOUNT_URL。設定後，這裡會顯示一鍵加入 LINE 官方帳號的連結。</p>"
    body = f"""
    <section class="hero">
      <div class="eyebrow">LINE Subscribe</div>
      <h1>每天用 LINE 收 2408 投資更新</h1>
      <p>會員點擊加入官方帳號後，只要在 LINE 對話輸入「綁定 會員ID」，系統會自動完成 LINE userId 綁定。</p>
      <div class="actions">{action}</div>
    </section>
    <section class="section">
      <h2>會員綁定方式</h2>
      <ol>
        <li>先點「加入 LINE 每日推播」。</li>
        <li>在 LINE 對話輸入：綁定 guest，或把 guest 換成你的會員 ID。</li>
        <li>看到「LINE 綁定完成」回覆後，即可接收後續推播。</li>
      </ol>
      <p>LINE 後台 Webhook URL：<code>{escape(webhook_url)}</code></p>
    </section>
    <section class="section">
      <h2>手動建立推播訂閱</h2>
      <form class="stack" method="post" action="/investment-plans/line-subscribe">
        <div class="grid">
          <label>會員 ID<input name="user_id" value="guest" required /></label>
          <label>計畫 ID<input name="plan_id" placeholder="可留空，或填入投資計畫 ID" /></label>
          <label>LINE userId<input name="line_user_id" placeholder="後台綁定後可填入，會員可先留空" /></label>
          <label>推播頻率<select name="frequency"><option value="daily" selected>每天</option><option value="weekly">每週</option></select></label>
        </div>
        <button type="submit">記錄 LINE 訂閱</button>
      </form>
    </section>
    <section class="section">
      <h2>推播內容</h2>
      <ul>
        <li>2408 最新股價與本月計畫建議。</li>
        <li>DRAM、DDR4、DDR5、矽晶圓、光阻、特用氣體等成本/供應鏈訊號。</li>
        <li>台塑、南亞科、日本客戶與合作消息等公開事件追蹤。</li>
        <li>提醒會員本月應買進、加碼、暫停、停利或做風險檢查。</li>
      </ul>
    </section>
    """
    return _plan_shell("LINE 每日推播訂閱", body)


@app.route("/investment-plans/line-subscribe", methods=["POST"])
def investment_line_subscribe_create():
    try:
        PLAN_STORE.create_line_subscription(LineSubscriptionRequest(
            user_id=request.form["user_id"],
            plan_id=_parse_uuid(request.form.get("plan_id")),
            line_user_id=request.form.get("line_user_id") or None,
            frequency=request.form.get("frequency", "daily"),
            consent=True,
        ))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 422
    return ("", 303, {"Location": f"/investment-plans/line-subscribe?user_id={escape(request.form['user_id'])}"})


@app.route("/investment-plans/line-webhook", methods=["GET"])
def investment_line_webhook_status():
    return jsonify({
        "status": "ok",
        "webhook": "ready",
        "requires": ["LINE_CHANNEL_SECRET", "LINE_CHANNEL_ACCESS_TOKEN"],
    })


@app.route("/investment-plans/line-webhook", methods=["POST"])
def investment_line_webhook():
    raw_body = request.get_data()
    signature = request.headers.get("X-Line-Signature")
    if not _verify_line_signature(raw_body, signature):
        return jsonify({"error": "invalid LINE signature"}), 403

    payload = request.get_json(force=True, silent=True) or {}
    handled = []
    for event in payload.get("events", []):
        event_type = event.get("type")
        source = event.get("source") or {}
        line_user_id = source.get("userId")
        reply_token = event.get("replyToken")

        if not line_user_id:
            handled.append({"type": event_type, "status": "ignored_without_user_id"})
            continue

        if event_type == "follow":
            _line_reply(reply_token, _line_binding_help())
            handled.append({"type": "follow", "status": "prompted_binding"})
            continue

        if event_type == "message" and (event.get("message") or {}).get("type") == "text":
            text = ((event.get("message") or {}).get("text") or "").strip()
            match = LINE_BINDING_RE.match(text)
            if match:
                member_id = match.group("user_id")
                subscription = _bind_line_user(line_user_id, member_id)
                _line_reply(
                    reply_token,
                    f"LINE 綁定完成。\n會員 ID：{member_id}\n狀態：{subscription.status}\n之後可接收 2408 每日投資更新。",
                )
                handled.append({"type": "message", "status": "bound", "user_id": member_id})
            else:
                _line_reply(reply_token, _line_binding_help())
                handled.append({"type": "message", "status": "prompted_binding"})
            continue

        handled.append({"type": event_type, "status": "ignored"})

    return jsonify({"status": "ok", "handled": handled})


@app.route("/investment-plans/<plan_id>")
def investment_plan_detail(plan_id):
    try:
        plan = PLAN_STORE.get(UUID(plan_id))
    except Exception:
        return jsonify({"error": "Investment plan not found"}), 404
    metrics = f"""
      <div class="metric"><div class="label">固定投入比例</div><div class="value">{plan.allocation.fixed_buy_ratio:.0%}</div></div>
      <div class="metric"><div class="label">現金保留比例</div><div class="value">{plan.allocation.cash_reserve_ratio:.0%}</div></div>
      <div class="metric"><div class="label">每月固定投入</div><div class="value">{_money(plan.allocation.monthly_fixed_buy_amount)}</div></div>
    """
    bands = "".join(f"<article class='card'><h3>{escape(item.name)}：{_money(item.price)}</h3><p>{escape(item.action)}</p></article>" for item in plan.price_bands)
    rules = "".join(f"<li>{escape(item.trigger)}：{escape(item.action)}</li>" for item in plan.action_rules)
    indicators = "".join(f"<li>{escape(item)}</li>" for item in plan.tracking_indicators)
    risks = "".join(f"<li>{escape(item)}</li>" for item in plan.risk_notes)
    latest = PLAN_STORE.latest_review(plan.id)
    latest_html = ""
    if latest:
        latest_html = f"<section class='section callout'><div class='eyebrow'>Latest Monthly Review</div><h2>{escape(latest.recommendation_label)}</h2><p>{escape(latest.summary)}</p><p>價格位置：{escape(latest.price_position)}；建議投入：{_money(latest.suggested_action_amount)}；保留現金：{_money(latest.suggested_cash_reserve)}</p></section>"
    body = f"""
    <section class="hero">
      <div class="eyebrow">{escape(plan.request.stock_symbol)} / {escape(plan.request.plan_type)}</div>
      <h1>{escape(plan.title)}</h1>
      <p>{escape(plan.summary)}</p>
      <div class="actions"><a class="btn" href="/investment-plans/{plan.id}/review">本月更新</a><a class="btn alt" href="/investment-plans/api/plans/{plan.id}">JSON</a></div>
    </section>
    {latest_html}
    <section class="section"><div class="grid three">{metrics}</div></section>
    <section class="section"><h2>適合度判斷</h2><p>{escape(plan.suitability)}</p></section>
    <section class="section"><h2>價格區間</h2><div class="grid">{bands}</div></section>
    <section class="section grid"><article><h2>操作規則</h2><ul>{rules}</ul></article><article><h2>每月追蹤</h2><ul>{indicators}</ul></article><article><h2>風險提醒</h2><ul>{risks}</ul></article></section>
    <section class="section"><h2>試算</h2><p>總計畫投入本金：{_money(plan.projection.invested_principal)}；目標停利參考價：{_money(plan.projection.target_take_profit_price)}；最大虧損檢查價：{_money(plan.projection.max_loss_review_price)}。</p><p>{escape(plan.disclosure)}</p></section>
    """
    return _plan_shell(plan.title, body)


@app.route("/investment-plans/<plan_id>/review")
def investment_plan_review_new(plan_id):
    try:
        plan = PLAN_STORE.get(UUID(plan_id))
    except Exception:
        return jsonify({"error": "Investment plan not found"}), 404
    avg = plan.request.average_cost or plan.request.current_price
    body = f"""
    <section class="hero"><div class="eyebrow">Monthly Review</div><h1>{escape(plan.title)} 本月更新</h1><p>輸入最新股價、可用現金與基本面趨勢，系統會判斷本月應該固定買進、加碼、暫停、停利或做風險檢查。</p></section>
    <section class="section"><form class="stack" method="post" action="/investment-plans/{plan.id}/reviews"><div class="grid">
      <label>最新股價<input type="number" step="0.01" name="current_price" value="{plan.request.current_price}" required /></label>
      <label>目前平均成本<input type="number" step="0.01" name="average_cost" value="{avg}" /></label>
      <label>目前持有股數<input type="number" step="0.0001" name="shares_owned" value="{plan.request.shares_owned}" /></label>
      <label>可用現金<input type="number" step="1" name="available_cash" value="{plan.request.monthly_amount or 0}" /></label>
      <label>月營收趨勢<select name="revenue_trend"><option value="unknown">未知</option><option value="improving">轉強</option><option value="stable" selected>穩定</option><option value="weakening">轉弱</option></select></label>
      <label>獲利趨勢<select name="earnings_trend"><option value="unknown">未知</option><option value="improving">轉強</option><option value="stable" selected>穩定</option><option value="weakening">轉弱</option></select></label>
      <label>原料/成本趨勢<select name="material_cost_trend"><option value="unknown">未知</option><option value="improving">成本改善</option><option value="stable" selected>穩定</option><option value="weakening">成本惡化</option></select></label>
      <label>全球同業/原料股趨勢<select name="global_peer_trend"><option value="unknown">未知</option><option value="improving">轉強</option><option value="stable" selected>穩定</option><option value="weakening">轉弱</option></select></label>
      <label>公開事件訊號<select name="public_event_signal"><option value="unknown">未知</option><option value="improving">正向</option><option value="stable" selected>中性</option><option value="weakening">負向</option></select></label>
      <label>估值狀態<select name="valuation_level"><option value="unknown">未知</option><option value="undervalued">偏低</option><option value="fair">合理</option><option value="expensive" selected>偏高</option><option value="overheated">過熱</option></select></label>
    </div><label>本月備註<input name="notes" placeholder="例如：財報公布、產業報價、個人現金流變化" /></label><button type="submit">產生本月建議</button></form></section>
    """
    return _plan_shell("本月投資更新", body)


@app.route("/investment-plans/<plan_id>/reviews", methods=["POST"])
def investment_plan_review_create(plan_id):
    try:
        avg = request.form.get("average_cost")
        PLAN_STORE.create_review(UUID(plan_id), PlanReviewRequest(
            current_price=float(request.form["current_price"]),
            average_cost=float(avg) if avg else None,
            shares_owned=float(request.form.get("shares_owned") or 0),
            available_cash=float(request.form.get("available_cash") or 0),
            revenue_trend=request.form.get("revenue_trend", "unknown"),
            earnings_trend=request.form.get("earnings_trend", "unknown"),
            material_cost_trend=request.form.get("material_cost_trend", "unknown"),
            global_peer_trend=request.form.get("global_peer_trend", "unknown"),
            public_event_signal=request.form.get("public_event_signal", "unknown"),
            valuation_level=request.form.get("valuation_level", "unknown"),
            notes=request.form.get("notes") or None,
        ))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 422
    return ("", 303, {"Location": f"/investment-plans/{plan_id}"})


@app.route("/investment-plans/api/plans", methods=["GET", "POST"])
def investment_plan_api_plans():
    if request.method == "POST":
        try:
            plan = PLAN_STORE.create(InvestmentPlanRequest(**(request.get_json(force=True, silent=True) or {})))
        except Exception as exc:
            return jsonify({"data": None, "error": str(exc)}), 422
        return jsonify({"data": plan.model_dump(mode="json"), "error": None})
    return jsonify({"data": [p.model_dump(mode="json") for p in PLAN_STORE.list_for_user(request.args.get("user_id"))], "error": None})


@app.route("/investment-plans/api/plans/<plan_id>")
def investment_plan_api_get(plan_id):
    try:
        plan = PLAN_STORE.get(UUID(plan_id))
    except Exception:
        return jsonify({"data": None, "error": "Investment plan not found"}), 404
    return jsonify({"data": plan.model_dump(mode="json"), "error": None})


@app.route("/investment-plans/api/plans/<plan_id>/reviews", methods=["GET", "POST"])
def investment_plan_api_reviews(plan_id):
    try:
        pid = UUID(plan_id)
        if request.method == "POST":
            review = PLAN_STORE.create_review(pid, PlanReviewRequest(**(request.get_json(force=True, silent=True) or {})))
            return jsonify({"data": review.model_dump(mode="json"), "error": None})
        PLAN_STORE.get(pid)
        return jsonify({"data": [r.model_dump(mode="json") for r in PLAN_STORE.list_reviews(pid)], "error": None})
    except Exception as exc:
        return jsonify({"data": None, "error": str(exc)}), 422


@app.route("/investment-plans/api/line-subscriptions", methods=["GET", "POST"])
def investment_line_subscription_api():
    if request.method == "POST":
        try:
            sub = PLAN_STORE.create_line_subscription(LineSubscriptionRequest(**(request.get_json(force=True, silent=True) or {})))
        except Exception as exc:
            return jsonify({"data": None, "error": str(exc)}), 422
        return jsonify({"data": sub.model_dump(mode="json"), "error": None})
    return jsonify({"data": [s.model_dump(mode="json") for s in PLAN_STORE.list_line_subscriptions(request.args.get("user_id"))], "error": None})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
