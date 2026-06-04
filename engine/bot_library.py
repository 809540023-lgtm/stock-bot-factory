# -*- coding: utf-8 -*-
"""
機器人資料庫 (bot_library.py)
================================================
平台第一期的「範例機器人產生器」核心資料。
這裡用資深分析師的角度，設計 8 個「策略邏輯互不重疊」的機器人，
涵蓋四大類操作風格 × 不同持有週期 × 不同風險等級，
讓使用者一鍵載入工廠後，能改成自己的版本。

每個機器人就是一份引擎吃的 JSON 規則（buy / sell），
另外附上給「人」看的中文標籤與分析師註記（前端直接顯示）。

可用指標（由 bot_engine.py 計算）：
  close（收盤）, kd_k, kd_d, rsi, sma_5, sma_20, sma_60, pct（日漲跌幅）
可用運算子：< , > , cross_above（向上穿越）, cross_below（向下跌破）
右邊可以是固定數字 value，也可以是另一條指標線 value_metric。
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from bot_engine import run_bot, DEFAULT_FEE_PCT


# ── 8 個機器人 ────────────────────────────────────────────
BOT_LIBRARY = [
    {
        "id": "kd_dip",
        "name": "KD 抄底機器人",
        "desc": "KD 的 K 值跌到 20 以下進場，漲到 80 以上出場。最經典的低買高賣。",
        "analyst_note": "適合箱型／盤整股。趨勢股上會太早賣、抱不住大波段。",
        "category": "抄底（均值回歸）",
        "risk": "中",
        "horizon": "短中線",
        "tags": ["KD", "抄底", "新手友善"],
        "buy_labels": ["KD 的 K < 20（超賣）"],
        "sell_labels": ["KD 的 K > 80（超買）"],
        "buy":  {"logic": "AND", "conditions": [{"metric": "kd_k", "op": "<", "value": 20}]},
        "sell": {"logic": "OR",  "conditions": [{"metric": "kd_k", "op": ">", "value": 80}]},
    },
    {
        "id": "ma20_trend",
        "name": "月線多頭機器人",
        "desc": "股價站上 20 日均線（月線）買進，跌破就賣。順勢操作的入門款。",
        "analyst_note": "趨勢明確時很穩；盤整時會被上下巴來回甩，產生連續小虧。",
        "category": "順勢（趨勢跟隨）",
        "risk": "中",
        "horizon": "中線",
        "tags": ["均線", "順勢", "新手友善"],
        "buy_labels": ["股價站上 20 日均線"],
        "sell_labels": ["股價跌破 20 日均線"],
        "buy":  {"logic": "AND", "conditions": [{"metric": "close", "op": "cross_above", "value_metric": "sma_20"}]},
        "sell": {"logic": "AND", "conditions": [{"metric": "close", "op": "cross_below", "value_metric": "sma_20"}]},
    },
    {
        "id": "rsi_reversal",
        "name": "RSI 反轉機器人",
        "desc": "RSI 跌破 30（超賣）進場，衝過 70（超買）出場。動能反轉經典。",
        "analyst_note": "與 KD 抄底類似但更平滑、訊號更少。強勢股可能整段都在 70 以上而錯過。",
        "category": "反轉（動能反轉）",
        "risk": "中",
        "horizon": "短中線",
        "tags": ["RSI", "反轉", "抄底"],
        "buy_labels": ["RSI < 30（超賣）"],
        "sell_labels": ["RSI > 70（超買）"],
        "buy":  {"logic": "AND", "conditions": [{"metric": "rsi", "op": "<", "value": 30}]},
        "sell": {"logic": "OR",  "conditions": [{"metric": "rsi", "op": ">", "value": 70}]},
    },
    {
        "id": "golden_cross",
        "name": "黃金交叉機器人",
        "desc": "5 日線向上穿越 20 日線（黃金交叉）買進，向下跌破（死亡交叉）賣出。",
        "analyst_note": "抓中波段趨勢轉折，反應比單一均線慢半拍但雜訊更少。",
        "category": "順勢（雙均線交叉）",
        "risk": "中",
        "horizon": "中線",
        "tags": ["均線", "黃金交叉", "順勢"],
        "buy_labels": ["5 日線黃金交叉 20 日線"],
        "sell_labels": ["5 日線死亡交叉 20 日線"],
        "buy":  {"logic": "AND", "conditions": [{"metric": "sma_5", "op": "cross_above", "value_metric": "sma_20"}]},
        "sell": {"logic": "AND", "conditions": [{"metric": "sma_5", "op": "cross_below", "value_metric": "sma_20"}]},
    },
    {
        "id": "season_momentum",
        "name": "季線動能機器人",
        "desc": "股價站上 60 日均線（季線）才進場，跌破 20 日線就走。吃中長波段。",
        "analyst_note": "進場門檻高、訊號少，但能參與大行情。適合大型權值股、ETF。",
        "category": "動能（長趨勢）",
        "risk": "中低",
        "horizon": "中長線",
        "tags": ["均線", "季線", "動能", "存股族"],
        "buy_labels": ["股價站上 60 日均線（季線）"],
        "sell_labels": ["股價跌破 20 日均線"],
        "buy":  {"logic": "AND", "conditions": [{"metric": "close", "op": "cross_above", "value_metric": "sma_60"}]},
        "sell": {"logic": "OR",  "conditions": [{"metric": "close", "op": "cross_below", "value_metric": "sma_20"}]},
    },
    {
        "id": "double_confirm_dip",
        "name": "雙重確認抄底機器人",
        "desc": "要 KD 的 K < 20「且」RSI < 35 同時成立才進場，超買任一觸發就出場。",
        "analyst_note": "兩個超賣指標互相確認，假訊號少、出手更謹慎，但進場機會也較稀有。",
        "category": "抄底（多重確認）",
        "risk": "低",
        "horizon": "短中線",
        "tags": ["KD", "RSI", "保守", "抄底"],
        "buy_labels": ["KD 的 K < 20", "且 RSI < 35"],
        "sell_labels": ["KD 的 K > 80", "或 RSI > 70"],
        "buy":  {"logic": "AND", "conditions": [
            {"metric": "kd_k", "op": "<", "value": 20},
            {"metric": "rsi", "op": "<", "value": 35}]},
        "sell": {"logic": "OR", "conditions": [
            {"metric": "kd_k", "op": ">", "value": 80},
            {"metric": "rsi", "op": ">", "value": 70}]},
    },
    {
        "id": "trend_filter_dip",
        "name": "順勢抄底機器人",
        "desc": "只在多頭格局（股價在季線之上）抄底：KD 的 K < 25 且 站在 60 日線上才買。",
        "analyst_note": "用季線當『多頭濾網』，避免在下跌段一直接刀。實務上很受用的組合。",
        "category": "抄底 + 趨勢濾網",
        "risk": "中",
        "horizon": "中線",
        "tags": ["KD", "均線濾網", "進階"],
        "buy_labels": ["KD 的 K < 25", "且 股價在 60 日線之上"],
        "sell_labels": ["KD 的 K > 80"],
        "buy":  {"logic": "AND", "conditions": [
            {"metric": "kd_k", "op": "<", "value": 25},
            {"metric": "close", "op": ">", "value_metric": "sma_60"}]},
        "sell": {"logic": "OR", "conditions": [{"metric": "kd_k", "op": ">", "value": 80}]},
    },
    {
        "id": "aggressive_short",
        "name": "短線積極機器人",
        "desc": "給想抓短波段的人：RSI < 35 且站上 5 日線就進，RSI 過 70 就跑。",
        "analyst_note": "出手頻繁、週期短，交易成本侵蝕大，務必看『含成本』後的數字。",
        "category": "動能（短線積極）",
        "risk": "高",
        "horizon": "短線",
        "tags": ["RSI", "短線", "積極", "高週轉"],
        "buy_labels": ["RSI < 35", "且 站上 5 日均線"],
        "sell_labels": ["RSI > 70"],
        "buy":  {"logic": "AND", "conditions": [
            {"metric": "rsi", "op": "<", "value": 35},
            {"metric": "close", "op": "cross_above", "value_metric": "sma_5"}]},
        "sell": {"logic": "OR", "conditions": [{"metric": "rsi", "op": ">", "value": 70}]},
    },
]


def get_bot(bot_id):
    """依 id 取出一個機器人定義。"""
    for b in BOT_LIBRARY:
        if b["id"] == bot_id:
            return b
    return None


def public_library():
    """回傳給前端的精簡清單（含規則 JSON 與顯示標籤，不含內部欄位）。"""
    return BOT_LIBRARY


# 向後相容：舊的 sample_bots 期待 SAMPLE_BOTS（name/desc/buy/sell）
SAMPLE_BOTS = [
    {"name": b["name"], "desc": b["desc"], "buy": b["buy"], "sell": b["sell"]}
    for b in BOT_LIBRARY
]


if __name__ == "__main__":
    # 用一段「有漲有跌再回升」的模擬股價自我驗證 8 個機器人都能跑、規則無誤
    import math
    prices = [round(100 + 15 * math.sin(t / 8) + (t - 60) * 0.3, 2) for t in range(160)]
    print(f"用 {len(prices)} 天模擬股價驗證 {len(BOT_LIBRARY)} 個機器人（含 {DEFAULT_FEE_PCT}% 來回成本）：\n")
    print(f"{'機器人':<18}{'類型':<16}{'風險':<5}{'交易':>4}{'勝率':>7}{'複利':>9}{'MDD':>8}")
    print("-" * 72)
    for b in BOT_LIBRARY:
        r = run_bot(b, prices, fee_pct=DEFAULT_FEE_PCT)
        print(f"{b['name']:<18}{b['category']:<16}{b['risk']:<5}{r.trades:>4}"
              f"{str(r.win_rate)+'%':>7}{str(r.compound_return_pct)+'%':>9}{str(r.max_drawdown_pct)+'%':>8}")
    print("-" * 72)
    print(f"同期買進持有基準：{run_bot(BOT_LIBRARY[0], prices).buy_hold_return_pct}%")
