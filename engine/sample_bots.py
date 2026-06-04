# -*- coding: utf-8 -*-
"""
範例機器人庫 + 測試。
這些 JSON 就是「範例機器人產生器」每期會丟給社群參考的範本。
使用者複製一份、改幾個數字，就變成自己的機器人。
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from bot_engine import run_bot

SAMPLE_BOTS = [
    {
        "name": "KD 抄底機器人",
        "desc": "KD 的 K 值跌到 20 以下進場，漲到 80 以上出場。經典低買高賣。",
        "buy":  {"logic": "AND", "conditions": [{"metric": "kd_k", "op": "<", "value": 20}]},
        "sell": {"logic": "OR",  "conditions": [{"metric": "kd_k", "op": ">", "value": 80}]},
    },
    {
        "name": "均線多頭機器人",
        "desc": "股價站上 20 日均線買進，跌破就賣。順勢操作。",
        "buy":  {"logic": "AND", "conditions": [{"metric": "close", "op": "cross_above", "value_metric": "sma_20"}]},
        "sell": {"logic": "AND", "conditions": [{"metric": "close", "op": "cross_below", "value_metric": "sma_20"}]},
    },
    {
        "name": "短線積極機器人",
        "desc": "給想抓短波段的人：RSI 低於 35 且站上 5 日線就進，RSI 過 70 就跑。",
        "buy":  {"logic": "AND", "conditions": [
            {"metric": "rsi", "op": "<", "value": 35},
            {"metric": "close", "op": "cross_above", "value_metric": "sma_5"}]},
        "sell": {"logic": "OR", "conditions": [{"metric": "rsi", "op": ">", "value": 70}]},
    },
]


def _fake_prices():
    """造一段有漲有跌的假股價，用來驗證引擎（沙盒連不到證交所）。"""
    import math
    base = 100
    out = []
    for t in range(120):
        # 一段下跌再回升的波動，加一點正弦讓 KD/RSI 有變化
        wave = 15 * math.sin(t / 8) + (t - 60) * 0.3
        out.append(round(base + wave, 2))
    return out


if __name__ == "__main__":
    prices = _fake_prices()
    print(f"用 {len(prices)} 天模擬股價測試三個範例機器人：\n")
    for bot in SAMPLE_BOTS:
        r = run_bot(bot, prices)
        print(f"【{bot['name']}】")
        print(f"  說明：{bot['desc']}")
        print(f"  交易 {r.trades} 次，勝率 {r.win_rate}%，累積報酬 {r.total_return_pct}%")
        print(f"  目前訊號：{r.final_signal}")
        if r.log:
            print(f"  最近一筆：{r.log[-1]}")
        print()
