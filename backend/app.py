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
    if set(reply_token) == {"0"}:
        return False
    try:
        resp = requests.post(
            "https://api.line.me/v2/bot/message/reply",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json={"replyToken": reply_token, "messages": [{"type": "text", "text": text[:5000]}]},
            timeout=2,
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


@app.route("/api/leaderboard")
def api_leaderboard():
    """
    一鍵把資料庫全部機器人跑在同一檔股票上，依複利報酬排名。
    重點：股價只抓一次（全部 bot 共用快取），所以比逐一回測快很多。
    另回傳每隻機器人「目前訊號」與標的的超買/超賣指標，供即時掃描用。
    """
    stock = request.args.get("stock", "2330").strip()
    if not _valid_stock(stock):
        return jsonify({"error": "股票代號格式不正確（請輸入 4 碼代號，如 2330）"}), 400
    months = request.args.get("months", 12)

    fee_pct = request.args.get("fee_pct", DEFAULT_FEE_PCT)
    try:
        fee_pct = max(0.0, float(fee_pct))
    except (ValueError, TypeError):
        fee_pct = DEFAULT_FEE_PCT

    closes = fetch_prices(stock, months)
    if len(closes) < 30:
        return jsonify({
            "error": f"資料不足（只抓到 {len(closes)} 天），無法比較。"
                     f"可能是代號錯誤、停牌或新上市。"
        }), 400

    ranking = []
    buy_hold = 0.0
    for bot in public_library():
        r = run_bot(bot, closes, fee_pct=fee_pct)
        buy_hold = r.buy_hold_return_pct
        ranking.append({
            "id": bot.get("id"),
            "name": bot.get("name"),
            "category": bot.get("category"),
            "risk": bot.get("risk"),
            "trades": r.trades,
            "win_rate": r.win_rate,
            "compound_return_pct": r.compound_return_pct,
            "max_drawdown_pct": r.max_drawdown_pct,
            "beats_buy_hold": r.beats_buy_hold,
            "final_signal": r.final_signal,
        })
    ranking.sort(key=lambda x: x["compound_return_pct"], reverse=True)

    return jsonify({
        "stock": stock,
        "days": len(closes),
        "last_close": closes[-1],
        "fee_pct": round(fee_pct, 3),
        "buy_hold_return_pct": buy_hold,
        "ranking": ranking,
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


LANG_LABELS = {
    "zh-TW": "繁體中文",
    "zh-CN": "简体中文",
    "ja": "日本語",
    "en": "English",
}
LANG_ALIASES = {
    "zh": "zh-TW",
    "zh-Hant": "zh-TW",
    "zh-Hans": "zh-CN",
    "tw": "zh-TW",
    "cn": "zh-CN",
    "jp": "ja",
}
TEXT = {
    "brand": {
        "zh-TW": "AI 單股投資計畫",
        "zh-CN": "AI 单股投资计划",
        "ja": "AI 個別株投資プラン",
        "en": "AI Single-Stock Plan",
    },
    "create_plan": {
        "zh-TW": "建立計畫",
        "zh-CN": "建立计划",
        "ja": "プラン作成",
        "en": "Create plan",
    },
    "home_title": {
        "zh-TW": "完整分析 + 每月零存整付",
        "zh-CN": "完整分析 + 每月定投",
        "ja": "詳細分析 + 毎月積立",
        "en": "Full analysis + monthly investing",
    },
    "home_intro": {
        "zh-TW": "會員可以針對單一股票建立完整分析計畫，也可以輸入每月 3,000 元、5,000 元或自訂金額，取得可追蹤的投入規則。",
        "zh-CN": "会员可以针对单一股票建立完整分析计划，也可以输入每月 3,000 元、5,000 元或自定义金额，取得可追踪的投入规则。",
        "ja": "会員は個別株の詳細分析プランを作成し、毎月 3,000 元、5,000 元、または任意金額の積立ルールを確認できます。",
        "en": "Members can create a full single-stock analysis plan or enter a monthly amount such as NT$3,000 or NT$5,000 to receive trackable investing rules.",
    },
    "recurring": {
        "zh-TW": "零存整付",
        "zh-CN": "定期定额",
        "ja": "毎月積立",
        "en": "Monthly plan",
    },
    "full_analysis": {
        "zh-TW": "完整分析",
        "zh-CN": "完整分析",
        "ja": "詳細分析",
        "en": "Full analysis",
    },
    "line_push": {
        "zh-TW": "LINE 每日推播",
        "zh-CN": "LINE 每日推送",
        "ja": "LINE 毎日通知",
        "en": "LINE daily alerts",
    },
    "plans_created": {
        "zh-TW": "已建立計畫",
        "zh-CN": "已建立计划",
        "ja": "作成済みプラン",
        "en": "Created plans",
    },
    "no_plans": {
        "zh-TW": "目前還沒有投資計畫。",
        "zh-CN": "目前还没有投资计划。",
        "ja": "投資プランはまだありません。",
        "en": "No investment plans yet.",
    },
    "view_plan": {
        "zh-TW": "查看計畫",
        "zh-CN": "查看计划",
        "ja": "プランを見る",
        "en": "View plan",
    },
    "tutorials": {
        "zh-TW": "教學影片",
        "zh-CN": "教学视频",
        "ja": "操作動画",
        "en": "Tutorial videos",
    },
    "tutorial_intro": {
        "zh-TW": "可直接播放的互動教學影片，會逐段顯示操作畫面、旁白與進度。",
        "zh-CN": "可直接播放的互动教学视频，会逐段显示操作画面、旁白与进度。",
        "ja": "その場で再生できる操作チュートリアルです。画面、ナレーション、進行状況を順番に表示します。",
        "en": "Playable interactive tutorials that step through screens, voice-over text, and progress.",
    },
    "watch_script": {
        "zh-TW": "播放",
        "zh-CN": "播放",
        "ja": "再生",
        "en": "Play",
    },
    "play": {
        "zh-TW": "播放",
        "zh-CN": "播放",
        "ja": "再生",
        "en": "Play",
    },
    "pause": {
        "zh-TW": "暫停",
        "zh-CN": "暂停",
        "ja": "一時停止",
        "en": "Pause",
    },
    "restart": {
        "zh-TW": "重播",
        "zh-CN": "重播",
        "ja": "最初から",
        "en": "Restart",
    },
    "interactive_video": {
        "zh-TW": "互動教學影片",
        "zh-CN": "互动教学视频",
        "ja": "インタラクティブ動画",
        "en": "Interactive video",
    },
    "create_title": {
        "zh-TW": "建立會員單股投資計畫",
        "zh-CN": "建立会员单股投资计划",
        "ja": "会員向け個別株プラン作成",
        "en": "Create a member stock plan",
    },
    "create_intro": {
        "zh-TW": "輸入股票、月存金額、風險偏好、原料與公開事件追蹤關鍵字，產生可每月更新的投資紀律。",
        "zh-CN": "输入股票、每月投入金额、风险偏好、原料与公开事件追踪关键词，生成可每月更新的投资纪律。",
        "ja": "銘柄、毎月金額、リスク許容度、材料と公開イベントの追跡キーワードを入力して、毎月更新できる投資ルールを作成します。",
        "en": "Enter the stock, monthly amount, risk profile, materials, and public-event keywords to generate an investment discipline you can review monthly.",
    },
    "line_title": {
        "zh-TW": "每天用 LINE 收 2408 投資更新",
        "zh-CN": "每天用 LINE 接收 2408 投资更新",
        "ja": "LINE で 2408 の毎日更新を受け取る",
        "en": "Receive daily 2408 updates on LINE",
    },
    "line_intro": {
        "zh-TW": "會員點擊加入官方帳號後，只要在 LINE 對話輸入「綁定 會員ID」，系統會自動完成 LINE userId 綁定。",
        "zh-CN": "会员点击加入官方账号后，只要在 LINE 对话输入「绑定 会员ID」，系统会自动完成 LINE userId 绑定。",
        "ja": "公式アカウントを追加した後、LINE で「綁定 会員ID」と入力すると、LINE userId が自動で紐づきます。",
        "en": "After adding the official account, members can type “bind memberID” in LINE to link their LINE userId automatically.",
    },
}

TUTORIAL_VIDEOS = [
    {
        "id": "start",
        "minutes": "01:00",
        "title": {
            "zh-TW": "一分鐘總入口導覽",
            "zh-CN": "一分钟总入口导览",
            "ja": "1分でわかる入口案内",
            "en": "One-minute portal tour",
        },
        "summary": {
            "zh-TW": "從總入口進入建立計畫、LINE 訂閱與教學區。",
            "zh-CN": "从总入口进入建立计划、LINE 订阅与教学区。",
            "ja": "入口からプラン作成、LINE 登録、チュートリアルへ進みます。",
            "en": "Use the portal to reach plan creation, LINE subscription, and tutorials.",
        },
        "steps": {
            "zh-TW": ["打開總入口。", "選擇零存整付或完整分析。", "右側可查看教學影片腳本。"],
            "zh-CN": ["打开总入口。", "选择定投或完整分析。", "右侧查看教学视频脚本。"],
            "ja": ["入口を開きます。", "積立または詳細分析を選びます。", "右側で動画台本を確認します。"],
            "en": ["Open the portal.", "Choose monthly plan or full analysis.", "Use the right panel for tutorial scripts."],
        },
    },
    {
        "id": "plan",
        "minutes": "02:00",
        "title": {
            "zh-TW": "建立 2408 投資計畫",
            "zh-CN": "建立 2408 投资计划",
            "ja": "2408 投資プランを作成",
            "en": "Create a 2408 investment plan",
        },
        "summary": {
            "zh-TW": "示範輸入會員 ID、股票、每月 3,000 元與風險條件。",
            "zh-CN": "示范输入会员 ID、股票、每月 3,000 元与风险条件。",
            "ja": "会員 ID、銘柄、毎月 3,000 元、リスク条件の入力を説明します。",
            "en": "Shows member ID, stock, NT$3,000 monthly amount, and risk inputs.",
        },
        "steps": {
            "zh-TW": ["點建立計畫。", "確認股票代號 2408、股票名稱南亞科。", "輸入每月投入金額與風險偏好。", "送出後查看價格區間與操作規則。"],
            "zh-CN": ["点击建立计划。", "确认股票代码 2408、股票名称南亚科。", "输入每月投入金额与风险偏好。", "送出后查看价格区间与操作规则。"],
            "ja": ["プラン作成を押します。", "銘柄コード 2408 と南亜科を確認します。", "毎月金額とリスク許容度を入力します。", "送信後、価格帯と操作ルールを確認します。"],
            "en": ["Click create plan.", "Confirm stock code 2408 and Nanya Technology.", "Enter monthly amount and risk profile.", "Submit and review price bands and action rules."],
        },
    },
    {
        "id": "line",
        "minutes": "01:30",
        "title": {
            "zh-TW": "LINE 綁定與每日推播",
            "zh-CN": "LINE 绑定与每日推送",
            "ja": "LINE 連携と毎日通知",
            "en": "LINE binding and daily alerts",
        },
        "summary": {
            "zh-TW": "教會員加入 LINE，並輸入「綁定 guest」完成綁定。",
            "zh-CN": "教会员加入 LINE，并输入「绑定 guest」完成绑定。",
            "ja": "LINE を追加し、「綁定 guest」と入力して連携します。",
            "en": "Members add LINE and type “bind guest” to complete binding.",
        },
        "steps": {
            "zh-TW": ["點加入 LINE 每日推播。", "在 LINE 對話輸入：綁定 guest。", "看到 LINE 綁定完成後，即可接收後續通知。"],
            "zh-CN": ["点击加入 LINE 每日推送。", "在 LINE 对话输入：绑定 guest。", "看到 LINE 绑定完成后，即可接收后续通知。"],
            "ja": ["LINE 毎日通知を追加します。", "LINE で「綁定 guest」と入力します。", "完了メッセージが出たら通知を受け取れます。"],
            "en": ["Tap LINE daily alerts.", "Type: bind guest.", "After confirmation, the member can receive future alerts."],
        },
    },
    {
        "id": "review",
        "minutes": "02:00",
        "title": {
            "zh-TW": "每月更新與風險檢查",
            "zh-CN": "每月更新与风险检查",
            "ja": "毎月更新とリスク確認",
            "en": "Monthly review and risk check",
        },
        "summary": {
            "zh-TW": "示範如何輸入最新股價、營收趨勢與估值狀態。",
            "zh-CN": "示范如何输入最新股价、营收趋势与估值状态。",
            "ja": "最新株価、売上傾向、バリュエーションを入力します。",
            "en": "Shows how to enter latest price, revenue trend, and valuation state.",
        },
        "steps": {
            "zh-TW": ["進入既有計畫。", "點本月更新。", "輸入最新股價與趨勢。", "查看本月買進、暫停、停利或風險檢查建議。"],
            "zh-CN": ["进入既有计划。", "点击本月更新。", "输入最新股价与趋势。", "查看本月买进、暂停、止盈或风险检查建议。"],
            "ja": ["既存プランを開きます。", "今月の更新を押します。", "最新価格と傾向を入力します。", "買い、停止、利確、リスク確認の提案を見ます。"],
            "en": ["Open an existing plan.", "Click monthly review.", "Enter latest price and trends.", "Review buy, pause, take-profit, or risk-check guidance."],
        },
    },
]


def _current_lang():
    raw = request.args.get("lang", "zh-TW")
    lang = LANG_ALIASES.get(raw, raw)
    return lang if lang in LANG_LABELS else "zh-TW"


def _with_lang(path, lang=None):
    lang = lang or _current_lang()
    separator = "&" if "?" in path else "?"
    return f"{path}{separator}lang={lang}"


def _t(key, lang=None):
    lang = lang or _current_lang()
    value = TEXT.get(key, {})
    return value.get(lang) or value.get("zh-TW") or key


def _localized(value, lang=None):
    lang = lang or _current_lang()
    return value.get(lang) or value.get("zh-TW") or ""


def _json_attr(value):
    import json
    return escape(json.dumps(value, ensure_ascii=False), quote=True)


def _lang_switcher():
    current = _current_lang()
    links = []
    base_path = request.path
    for lang, label in LANG_LABELS.items():
        cls = "active" if lang == current else ""
        links.append(f"<a class='{cls}' href='{escape(_with_lang(base_path, lang))}'>{escape(label)}</a>")
    return "".join(links)


def _tutorial_sidebar(lang=None):
    lang = lang or _current_lang()
    cards = "".join(
        f"""
        <article class="video-card">
          <div class="video-thumb"><span>{escape(item["minutes"])}</span></div>
          <h3>{escape(_localized(item["title"], lang))}</h3>
          <p>{escape(_localized(item["summary"], lang))}</p>
          <a class="text-link" href="{escape(_with_lang(f'/investment-plans/tutorials/{item["id"]}', lang))}">▶ {escape(_t("watch_script", lang))}</a>
        </article>
        """
        for item in TUTORIAL_VIDEOS
    )
    return f"""
    <aside class="tutorial-rail" aria-label="{escape(_t("tutorials", lang))}">
      <div class="rail-head">
        <div class="eyebrow">Tutorial</div>
        <h2>{escape(_t("tutorials", lang))}</h2>
        <p>{escape(_t("tutorial_intro", lang))}</p>
      </div>
      {cards}
    </aside>
    """


def _plan_shell(title, body):
    lang = _current_lang()
    return f"""<!doctype html>
<html lang="{escape(lang)}">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{escape(title)}</title>
  <style>
    :root {{ --bg:#f7f8f2; --panel:#fff; --ink:#17201b; --muted:#5d6b63; --line:#d9dfd7; --accent:#25665b; --accent2:#9b4f2f; --soft:#eef3eb; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:"PingFang TC",system-ui,sans-serif; background:var(--bg); color:var(--ink); }}
    .wrap {{ max-width:1320px; margin:0 auto; padding:28px 20px 64px; }}
    .topbar {{ display:flex; justify-content:space-between; gap:14px; align-items:center; margin-bottom:18px; }}
    .brand {{ color:var(--accent); font-weight:800; text-decoration:none; }}
    .nav {{ display:flex; gap:10px; flex-wrap:wrap; align-items:center; }}
    .lang {{ display:flex; gap:6px; flex-wrap:wrap; }}
    .lang a {{ color:var(--muted); border:1px solid var(--line); border-radius:999px; padding:7px 10px; text-decoration:none; font-size:13px; background:#fff; }}
    .lang a.active {{ color:#fff; background:var(--accent); border-color:var(--accent); }}
    .page-grid {{ display:grid; grid-template-columns:minmax(0,1fr) 330px; gap:18px; align-items:start; }}
    main {{ min-width:0; }}
    .hero,.section {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:22px; }}
    .section {{ margin-top:16px; }}
    .tutorial-rail {{ position:sticky; top:18px; display:grid; gap:12px; }}
    .rail-head,.video-card {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:16px; }}
    .video-thumb {{ aspect-ratio:16/9; border-radius:8px; background:linear-gradient(135deg,#1f5c51,#9b4f2f); display:flex; align-items:flex-end; justify-content:flex-end; padding:10px; color:#fff; font-weight:800; margin-bottom:12px; }}
    .tutorial-player {{ border:1px solid var(--line); border-radius:8px; overflow:hidden; background:#101815; color:#fff; }}
    .player-screen {{ min-height:320px; display:grid; align-content:center; gap:18px; padding:28px; background:linear-gradient(135deg,#10201b,#25665b 58%,#9b4f2f); }}
    .screen-kicker {{ color:#d9f1e7; font-size:13px; font-weight:800; letter-spacing:.08em; text-transform:uppercase; }}
    .screen-title {{ color:#fff; margin:0; font-size:32px; line-height:1.15; }}
    .screen-text {{ color:#eef8f3; font-size:18px; margin:0; }}
    .progress-track {{ height:8px; background:#26342f; }}
    .progress-bar {{ height:100%; width:0%; background:#4dd19b; transition:width .25s ease; }}
    .player-controls {{ display:flex; gap:10px; flex-wrap:wrap; align-items:center; justify-content:space-between; padding:14px; background:#17201b; }}
    .player-controls .buttons {{ display:flex; gap:8px; flex-wrap:wrap; }}
    .player-controls button {{ background:#fff; color:#17201b; padding:9px 12px; }}
    .step-count {{ color:#cfe4d9; font-weight:800; }}
    .text-link {{ color:var(--accent); font-weight:800; text-decoration:none; }}
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
    @media(max-width:980px) {{ .page-grid {{ grid-template-columns:1fr; }} .tutorial-rail {{ position:static; }} }}
    @media(max-width:820px) {{ h1 {{ font-size:30px; }} .grid,.grid.three {{ grid-template-columns:1fr; }} .topbar {{ align-items:flex-start; flex-direction:column; }} }}
  </style>
</head>
<body><div class="wrap">
  <div class="topbar">
    <a class="brand" href="{escape(_with_lang("/investment-plans", lang))}">{escape(_t("brand", lang))}</a>
    <div class="nav">
      <div class="lang">{_lang_switcher()}</div>
      <a class="btn alt" href="{escape(_with_lang("/investment-plans/new", lang))}">{escape(_t("create_plan", lang))}</a>
    </div>
  </div>
  <div class="page-grid">
    <main>{body}</main>
    {_tutorial_sidebar(lang)}
  </div>
</div></body></html>"""


@app.route("/investment-plans")
def investment_plan_home():
    lang = _current_lang()
    user_id = request.args.get("user_id")
    plans = PLAN_STORE.list_for_user(user_id)
    cards = "".join(
        "<article class='card'>"
        f"<div class='eyebrow'>{escape(plan.request.plan_type)}</div>"
        f"<h3>{escape(plan.title)}</h3>"
        f"<p>{escape(plan.summary)}</p>"
        f"<div class='actions'><a class='btn' href='{escape(_with_lang(f'/investment-plans/{plan.id}', lang))}'>{escape(_t('view_plan', lang))}</a></div>"
        "</article>"
        for plan in plans[:12]
    ) or f"<p>{escape(_t('no_plans', lang))}</p>"
    body = f"""
    <section class="hero">
      <div class="eyebrow">Single Stock Planning</div>
      <h1>{escape(_t("home_title", lang))}</h1>
      <p>{escape(_t("home_intro", lang))}</p>
      <div class="actions">
        <a class="btn" href="{escape(_with_lang('/investment-plans/new?plan_type=recurring_investment', lang))}">{escape(_t("recurring", lang))}</a>
        <a class="btn alt" href="{escape(_with_lang('/investment-plans/new?plan_type=full_analysis', lang))}">{escape(_t("full_analysis", lang))}</a>
        <a class="btn" href="{escape(_with_lang('/investment-plans/line-subscribe', lang))}">{escape(_t("line_push", lang))}</a>
      </div>
    </section>
    <section class="section"><h2>{escape(_t("plans_created", lang))}</h2><div class="grid">{cards}</div></section>
    """
    return _plan_shell(_t("brand", lang), body)


@app.route("/investment-plans/tutorials")
def investment_tutorials_index():
    lang = _current_lang()
    cards = "".join(
        f"""
        <article class="card">
          <div class="eyebrow">{escape(item["minutes"])}</div>
          <h3>{escape(_localized(item["title"], lang))}</h3>
          <p>{escape(_localized(item["summary"], lang))}</p>
          <div class="actions"><a class="btn" href="{escape(_with_lang(f'/investment-plans/tutorials/{item["id"]}', lang))}">{escape(_t("watch_script", lang))}</a></div>
        </article>
        """
        for item in TUTORIAL_VIDEOS
    )
    body = f"""
    <section class="hero">
      <div class="eyebrow">Tutorial Library</div>
      <h1>{escape(_t("tutorials", lang))}</h1>
      <p>{escape(_t("tutorial_intro", lang))}</p>
    </section>
    <section class="section"><div class="grid">{cards}</div></section>
    """
    return _plan_shell(_t("tutorials", lang), body)


@app.route("/investment-plans/tutorials/<video_id>")
def investment_tutorial_detail(video_id):
    lang = _current_lang()
    item = next((video for video in TUTORIAL_VIDEOS if video["id"] == video_id), None)
    if not item:
        return jsonify({"error": "Tutorial not found"}), 404
    localized_steps = _localized(item["steps"], lang)
    steps = "".join(f"<li>{escape(step)}</li>" for step in localized_steps)
    player_steps = [
        {
            "kicker": f"{index + 1} / {len(localized_steps)}",
            "title": _localized(item["title"], lang),
            "text": step,
        }
        for index, step in enumerate(localized_steps)
    ]
    body = f"""
    <section class="hero">
      <div class="eyebrow">{escape(_t("interactive_video", lang))} / {escape(item["minutes"])}</div>
      <h1>{escape(_localized(item["title"], lang))}</h1>
      <p>{escape(_localized(item["summary"], lang))}</p>
      <div class="actions">
        <a class="btn" href="{escape(_with_lang('/investment-plans', lang))}">{escape(_t("brand", lang))}</a>
        <a class="btn alt" href="{escape(_with_lang('/investment-plans/tutorials', lang))}">{escape(_t("tutorials", lang))}</a>
      </div>
    </section>
    <section class="section">
      <div class="tutorial-player" data-steps="{_json_attr(player_steps)}">
        <div class="player-screen">
          <div class="screen-kicker" data-role="kicker">1 / {len(localized_steps)}</div>
          <h2 class="screen-title" data-role="title">{escape(_localized(item["title"], lang))}</h2>
          <p class="screen-text" data-role="text">{escape(localized_steps[0])}</p>
        </div>
        <div class="progress-track"><div class="progress-bar" data-role="progress"></div></div>
        <div class="player-controls">
          <div class="buttons">
            <button type="button" data-action="play">▶ {escape(_t("play", lang))}</button>
            <button type="button" data-action="pause">{escape(_t("pause", lang))}</button>
            <button type="button" data-action="restart">{escape(_t("restart", lang))}</button>
          </div>
          <div class="step-count" data-role="count">1 / {len(localized_steps)}</div>
        </div>
      </div>
    </section>
    <section class="section">
      <h2>Storyboard</h2>
      <ol>{steps}</ol>
    </section>
    <section class="section">
      <h2>Voice-over</h2>
      <p>{escape(" / ".join(_localized(item["steps"], lang)))}</p>
    </section>
    <script>
    (() => {{
      const player = document.querySelector('.tutorial-player');
      if (!player) return;
      const steps = JSON.parse(player.dataset.steps || '[]');
      const kicker = player.querySelector('[data-role="kicker"]');
      const title = player.querySelector('[data-role="title"]');
      const text = player.querySelector('[data-role="text"]');
      const progress = player.querySelector('[data-role="progress"]');
      const count = player.querySelector('[data-role="count"]');
      let index = 0;
      let timer = null;
      const render = () => {{
        const step = steps[index] || steps[0];
        if (!step) return;
        kicker.textContent = step.kicker;
        title.textContent = step.title;
        text.textContent = step.text;
        count.textContent = `${{index + 1}} / ${{steps.length}}`;
        progress.style.width = `${{((index + 1) / steps.length) * 100}}%`;
      }};
      const pause = () => {{
        if (timer) window.clearInterval(timer);
        timer = null;
      }};
      const play = () => {{
        pause();
        timer = window.setInterval(() => {{
          if (index >= steps.length - 1) {{
            pause();
            return;
          }}
          index += 1;
          render();
        }}, 2200);
      }};
      player.querySelector('[data-action="play"]').addEventListener('click', play);
      player.querySelector('[data-action="pause"]').addEventListener('click', pause);
      player.querySelector('[data-action="restart"]').addEventListener('click', () => {{
        pause();
        index = 0;
        render();
        play();
      }});
      render();
    }})();
    </script>
    """
    return _plan_shell(_localized(item["title"], lang), body)


@app.route("/investment-plans/new")
def investment_plan_new():
    lang = _current_lang()
    plan_type = request.args.get("plan_type", "recurring_investment")
    recurring_selected = "selected" if plan_type == "recurring_investment" else ""
    full_selected = "selected" if plan_type == "full_analysis" else ""
    body = f"""
    <section class="hero">
      <div class="eyebrow">Create Plan</div>
      <h1>{escape(_t("create_title", lang))}</h1>
      <p>{escape(_t("create_intro", lang))}</p>
    </section>
    <section class="section">
      <form class="stack" method="post" action="{escape(_with_lang('/investment-plans/create', lang))}">
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
    return _plan_shell(_t("create_title", lang), body)


@app.route("/investment-plans/create", methods=["POST"])
def investment_plan_create():
    lang = _current_lang()
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
    return ("", 303, {"Location": _with_lang(f"/investment-plans/{plan.id}", lang)})


@app.route("/investment-plans/line-subscribe")
def investment_line_subscribe():
    lang = _current_lang()
    line_url = os.environ.get("LINE_OFFICIAL_ACCOUNT_URL", "")
    webhook_url = os.environ.get("LINE_WEBHOOK_URL", "https://stock-bot-backend-kcbc.onrender.com/investment-plans/line-webhook")
    action = f"<a class='btn' href='{escape(line_url)}'>加入 LINE 每日推播</a>" if line_url else "<p>尚未設定 LINE_OFFICIAL_ACCOUNT_URL。設定後，這裡會顯示一鍵加入 LINE 官方帳號的連結。</p>"
    body = f"""
    <section class="hero">
      <div class="eyebrow">LINE Subscribe</div>
      <h1>{escape(_t("line_title", lang))}</h1>
      <p>{escape(_t("line_intro", lang))}</p>
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
      <form class="stack" method="post" action="{escape(_with_lang('/investment-plans/line-subscribe', lang))}">
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
    return _plan_shell(_t("line_title", lang), body)


@app.route("/investment-plans/line-subscribe", methods=["POST"])
def investment_line_subscribe_create():
    lang = _current_lang()
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
    return ("", 303, {"Location": _with_lang(f"/investment-plans/line-subscribe?user_id={escape(request.form['user_id'])}", lang)})


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

        if str(event.get("webhookEventId", "")).startswith("dummy"):
            handled.append({"type": event_type, "status": "verified_dummy_event"})
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
