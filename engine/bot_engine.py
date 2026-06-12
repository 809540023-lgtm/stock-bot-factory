# -*- coding: utf-8 -*-
"""
機器人工廠核心引擎 (bot_engine.py)
================================================
平台的心臟。每個使用者的「AI 股票機器人」就是一份 JSON 規則，
這支引擎負責：
  1. 算技術指標（KD、均線、RSI、漲跌幅）
  2. 把 JSON 規則翻譯成「買進/賣出/觀望」訊號
  3. 用歷史資料回測，算出這個機器人過去的表現

設計重點：使用者用積木拖出規則 -> 前端存成這份 JSON -> 這支引擎執行。
不懂程式的人完全不碰程式碼，只碰積木。
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any


# ── 技術指標 ───────────────────────────────────────────────
def sma(prices: List[float], n: int) -> List[float]:
    """簡單移動平均。前 n-1 天沒有值，補 None。"""
    out = [None] * len(prices)
    for i in range(n - 1, len(prices)):
        out[i] = sum(prices[i - n + 1:i + 1]) / n
    return out


def rsi(prices: List[float], n: int = 14) -> List[float]:
    """相對強弱指標 RSI。"""
    out = [None] * len(prices)
    gains, losses = [], []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
        if i >= n:
            ag = sum(gains[-n:]) / n
            al = sum(losses[-n:]) / n
            out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def kd(prices: List[float], n: int = 9) -> Dict[str, List[float]]:
    """KD 指標（簡化版，用收盤價當高低點近似）。回傳 K 與 D 兩條線。"""
    rsv = [None] * len(prices)
    for i in range(n - 1, len(prices)):
        window = prices[i - n + 1:i + 1]
        low, high = min(window), max(window)
        rsv[i] = 50.0 if high == low else (prices[i] - low) / (high - low) * 100
    k = [None] * len(prices)
    d = [None] * len(prices)
    k_prev, d_prev = 50.0, 50.0
    for i in range(len(prices)):
        if rsv[i] is None:
            continue
        k_prev = k_prev * 2 / 3 + rsv[i] / 3
        d_prev = d_prev * 2 / 3 + k_prev / 3
        k[i], d[i] = k_prev, d_prev
    return {"k": k, "d": d}


def pct_change(prices: List[float]) -> List[float]:
    """每日漲跌幅 %。"""
    out = [None]
    for i in range(1, len(prices)):
        out.append((prices[i] / prices[i - 1] - 1) * 100 if prices[i - 1] else None)
    return out


def ema(prices: List[float], n: int) -> List[float]:
    """指數移動平均。從第一筆開始遞推，回傳與 prices 等長（不補 None）。"""
    if not prices:
        return []
    k = 2 / (n + 1)
    out = [prices[0]]
    for i in range(1, len(prices)):
        out.append(prices[i] * k + out[i - 1] * (1 - k))
    return out


def rolling_std(prices: List[float], n: int) -> List[float]:
    """滾動「母體」標準差（除以 N，不是 N-1）。前 n-1 天沒有值，補 None。"""
    out = [None] * len(prices)
    for i in range(n - 1, len(prices)):
        window = prices[i - n + 1:i + 1]
        mean = sum(window) / n
        out[i] = (sum((x - mean) ** 2 for x in window) / n) ** 0.5
    return out


def macd(prices: List[float], fast: int = 12, slow: int = 26,
         signal: int = 9) -> Dict[str, List[float]]:
    """MACD。dif = EMA(fast) - EMA(slow)；signal = dif 的 EMA(signal)；hist = dif - signal。"""
    ema_fast = ema(prices, fast)
    ema_slow = ema(prices, slow)
    dif = [f - s for f, s in zip(ema_fast, ema_slow)]
    sig = ema(dif, signal)
    hist = [d - s for d, s in zip(dif, sig)]
    return {"dif": dif, "signal": sig, "hist": hist}


def bollinger(prices: List[float], n: int = 20, k: float = 2.0) -> Dict[str, List[float]]:
    """布林通道。中軌 = n 日 SMA；上下軌 = 中軌 ± k 倍滾動母體標準差。前 n-1 天補 None。"""
    mid = sma(prices, n)
    std = rolling_std(prices, n)
    up = [None] * len(prices)
    low = [None] * len(prices)
    for i in range(len(prices)):
        if mid[i] is not None and std[i] is not None:
            up[i] = mid[i] + k * std[i]
            low[i] = mid[i] - k * std[i]
    return {"up": up, "mid": mid, "low": low}


# ── 規則判斷 ───────────────────────────────────────────────
def _value_at(indicators: Dict[str, Any], key: str, i: int):
    """從算好的指標表取第 i 天的值。key 例如 'kd_k'、'sma_20'、'rsi'、'pct'。"""
    series = indicators.get(key)
    if series is None:
        return None
    return series[i] if i < len(series) else None


def eval_condition(cond: Dict[str, Any], indicators: Dict[str, Any], i: int) -> bool:
    """
    判斷單一條件積木是否成立。一塊積木長這樣：
      {"metric": "kd_k", "op": "<", "value": 20}
      {"metric": "close", "op": "cross_below", "value_metric": "sma_20"}
    支援 op: <, >, cross_above, cross_below
    """
    left = _value_at(indicators, cond["metric"], i)
    if left is None:
        return False

    op = cond["op"]
    # 右邊可以是固定值，也可以是另一條指標線
    if "value_metric" in cond:
        right = _value_at(indicators, cond["value_metric"], i)
        right_prev = _value_at(indicators, cond["value_metric"], i - 1)
    else:
        right = cond.get("value")
        right_prev = right
    if right is None:
        return False

    if op == "<":
        return left < right
    if op == ">":
        return left > right
    if op in ("cross_above", "cross_below"):
        left_prev = _value_at(indicators, cond["metric"], i - 1)
        if left_prev is None or right_prev is None:
            return False
        if op == "cross_above":   # 由下往上穿越
            return left_prev <= right_prev and left > right
        else:                     # 由上往下跌破
            return left_prev >= right_prev and left < right
    return False


def eval_rule_group(group: Dict[str, Any], indicators: Dict[str, Any], i: int) -> bool:
    """
    一組條件用 AND 或 OR 串起來。group 長這樣：
      {"logic": "AND", "conditions": [cond1, cond2, ...]}
    """
    results = [eval_condition(c, indicators, i) for c in group["conditions"]]
    if not results:
        return False
    return all(results) if group.get("logic", "AND") == "AND" else any(results)


# ── 機器人定義 + 回測 ──────────────────────────────────────
# 台股「來回」交易成本預設值（%）：
#   手續費 0.1425% × 2（買+賣，保守不打折）+ 賣出證交稅 0.3%
#   = 0.285% + 0.3% = 0.585%。回測務必扣掉，否則勝率與報酬都會被高估。
DEFAULT_FEE_PCT = 0.585


@dataclass
class BacktestResult:
    trades: int = 0
    wins: int = 0
    total_return_pct: float = 0.0      # 各筆「淨」報酬相加（與舊版同語意，只是已扣成本）
    final_signal: str = "觀望"
    log: List[str] = field(default_factory=list)
    # ── 以下為強化欄位（資深分析師視角，誠實揭露風險）──
    compound_return_pct: float = 0.0   # 複利累積報酬（每筆滾入，較貼近真實資金成長）
    max_drawdown_pct: float = 0.0      # 最大回撤：權益曲線從高點摔下來最深的幅度
    buy_hold_return_pct: float = 0.0   # 同期「買進並抱著」的報酬，當作比較基準
    avg_return_pct: float = 0.0        # 平均每筆報酬
    fee_pct: float = 0.0               # 本次回測採用的來回成本
    markers: List[Dict[str, Any]] = field(default_factory=list)  # 買賣點標記（畫圖用）

    @property
    def win_rate(self):
        return round(self.wins / self.trades * 100, 1) if self.trades else 0.0

    @property
    def beats_buy_hold(self):
        """有沒有贏過「無腦買進持有」。贏不過代表這個機器人沒創造價值。"""
        return self.compound_return_pct > self.buy_hold_return_pct


def run_bot(bot: Dict[str, Any], prices: List[float],
            fee_pct: float = 0.0) -> BacktestResult:
    """
    執行一個機器人。bot 是使用者用積木組出來的 JSON：
      {
        "name": "我的KD抄底機器人",
        "buy":  {"logic":"AND","conditions":[{"metric":"kd_k","op":"<","value":20}]},
        "sell": {"logic":"OR", "conditions":[{"metric":"kd_k","op":">","value":80}]}
      }
    fee_pct：每筆來回交易扣掉的成本（%）。預設 0 維持與舊版完全一致的行為；
             正式回測建議帶 DEFAULT_FEE_PCT（台股約 0.585%）。
    回傳回測結果。
    """
    if not prices:
        return BacktestResult(fee_pct=round(fee_pct, 3))

    # 先算好所有可能用到的指標
    kd_v = kd(prices)
    macd_v = macd(prices)
    boll_v = bollinger(prices)
    indicators = {
        "close": prices,
        "kd_k": kd_v["k"],
        "kd_d": kd_v["d"],
        "rsi": rsi(prices),
        "sma_5": sma(prices, 5),
        "sma_20": sma(prices, 20),
        "sma_60": sma(prices, 60),
        "pct": pct_change(prices),
        "macd": macd_v["dif"],
        "macd_signal": macd_v["signal"],
        "macd_hist": macd_v["hist"],
        "boll_up": boll_v["up"],
        "boll_mid": boll_v["mid"],
        "boll_low": boll_v["low"],
    }

    res = BacktestResult(fee_pct=round(fee_pct, 3))
    holding = False
    buy_price = 0.0
    equity = 1.0          # 權益曲線（複利），用來算最大回撤
    peak = 1.0
    max_dd = 0.0

    for i in range(1, len(prices)):
        if not holding and eval_rule_group(bot["buy"], indicators, i):
            holding = True
            buy_price = prices[i]
            res.markers.append({"i": i, "type": "buy", "price": prices[i]})
            res.log.append(f"第{i}天 買進 @ {prices[i]:.2f}")
        elif holding and eval_rule_group(bot["sell"], indicators, i):
            holding = False
            ret = (prices[i] / buy_price - 1) * 100 - fee_pct   # 扣掉來回成本
            res.trades += 1
            if ret > 0:
                res.wins += 1
            res.total_return_pct += ret
            equity *= (1 + ret / 100)
            peak = max(peak, equity)
            max_dd = max(max_dd, (peak - equity) / peak * 100)
            res.markers.append({"i": i, "type": "sell", "price": prices[i]})
            res.log.append(f"第{i}天 賣出 @ {prices[i]:.2f}（這筆 {ret:+.1f}%，含成本）")

    # 最後一天給出當下訊號
    if eval_rule_group(bot["buy"], indicators, len(prices) - 1):
        res.final_signal = "買進"
    elif holding and eval_rule_group(bot["sell"], indicators, len(prices) - 1):
        res.final_signal = "賣出"
    elif holding:
        res.final_signal = "續抱"

    res.total_return_pct = round(res.total_return_pct, 1)
    res.compound_return_pct = round((equity - 1) * 100, 1)
    res.max_drawdown_pct = round(max_dd, 1)
    res.buy_hold_return_pct = round((prices[-1] / prices[0] - 1) * 100, 1)
    res.avg_return_pct = round(res.total_return_pct / res.trades, 1) if res.trades else 0.0
    return res
