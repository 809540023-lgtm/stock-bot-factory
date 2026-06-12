from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4
from datetime import datetime

from investment_plans.schemas import (
    ActionRule,
    AllocationPlan,
    InvestmentPlanRecord,
    InvestmentPlanRequest,
    InvestmentProjection,
    LineSubscriptionRecord,
    LineSubscriptionRequest,
    PlanReviewRecord,
    PlanReviewRequest,
    PriceBand,
)


DISCLOSURE = "本計畫為教育與紀律規劃用途，不構成買賣建議；會員仍需自行判斷並承擔投資風險。"


def _now() -> datetime:
    return datetime.now().astimezone()


def _round_money(value: float) -> float:
    return round(value, 2)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


class InvestmentPlanEngine:
    def build_plan(self, request: InvestmentPlanRequest) -> InvestmentPlanRecord:
        reserve_ratio = self._cash_reserve_ratio(request)
        fixed_buy_ratio = round(1 - reserve_ratio, 2)
        monthly_fixed = None
        monthly_reserve = None
        monthly_shares = None
        if request.monthly_amount:
            monthly_fixed = _round_money(request.monthly_amount * fixed_buy_ratio)
            monthly_reserve = _round_money(request.monthly_amount * reserve_ratio)
            monthly_shares = round(monthly_fixed / request.current_price, 4)

        total_contribution = request.initial_amount + ((request.monthly_amount or 0) * 12 * request.investment_years)
        allocation = AllocationPlan(
            monthly_amount=request.monthly_amount,
            fixed_buy_ratio=fixed_buy_ratio,
            cash_reserve_ratio=reserve_ratio,
            monthly_fixed_buy_amount=monthly_fixed,
            monthly_cash_reserve_amount=monthly_reserve,
            estimated_monthly_shares=monthly_shares,
            total_planned_contribution=_round_money(total_contribution),
        )

        title = self._title(request)
        summary = self._summary(request, allocation)
        suitability = self._suitability(request)
        price_bands = self._price_bands(request)
        action_rules = self._action_rules(request, allocation)
        tracking_indicators = self._tracking_indicators(request)
        risk_notes = self._risk_notes(request)
        next_review_items = self._next_review_items(request)
        projection = self._projection(request, allocation)
        timestamp = _now()

        return InvestmentPlanRecord(
            id=uuid4(),
            created_at=timestamp,
            updated_at=timestamp,
            request=request,
            title=title,
            summary=summary,
            suitability=suitability,
            allocation=allocation,
            price_bands=price_bands,
            action_rules=action_rules,
            tracking_indicators=tracking_indicators,
            risk_notes=risk_notes,
            next_review_items=next_review_items,
            projection=projection,
            disclosure=DISCLOSURE,
        )

    def _cash_reserve_ratio(self, request: InvestmentPlanRequest) -> float:
        by_cycle = {
            "stable": {"conservative": 0.2, "balanced": 0.1, "aggressive": 0.0},
            "cyclical": {"conservative": 0.5, "balanced": 0.4, "aggressive": 0.3},
            "growth": {"conservative": 0.4, "balanced": 0.3, "aggressive": 0.2},
            "unknown": {"conservative": 0.35, "balanced": 0.25, "aggressive": 0.15},
        }
        ratio = by_cycle[request.industry_cycle][request.risk_profile]
        if request.valuation_level == "undervalued":
            ratio -= 0.1
        elif request.valuation_level == "expensive":
            ratio += 0.15
        elif request.valuation_level == "overheated":
            ratio += 0.25
        if not request.allow_high_volatility and request.industry_cycle in {"cyclical", "growth"}:
            ratio += 0.1
        return round(_clamp(ratio, 0.0, 0.75), 2)

    def _title(self, request: InvestmentPlanRequest) -> str:
        stock = request.stock_name or request.stock_symbol
        if request.plan_type == "recurring_investment":
            return f"{stock} 每月零存整付投資計畫"
        return f"{stock} 完整股票分析計畫"

    def _summary(self, request: InvestmentPlanRequest, allocation: AllocationPlan) -> str:
        stock = request.stock_name or request.stock_symbol
        if request.plan_type == "recurring_investment":
            return (
                f"以每月 {request.monthly_amount:,.0f} 元規劃 {request.investment_years} 年，"
                f"建議固定投入 {allocation.fixed_buy_ratio:.0%}、保留現金 {allocation.cash_reserve_ratio:.0%}，"
                f"避免在單股高估或產業循環高峰時把預算一次打滿。"
            )
        return (
            f"{stock} 的完整分析計畫會先建立買進、加碼、暫停、停利與停損規則，"
            "再用每月追蹤指標確認長期持有條件是否仍成立。"
        )

    def _suitability(self, request: InvestmentPlanRequest) -> str:
        if request.valuation_level == "overheated":
            return "目前偏向觀察或低比例投入，除非後續基本面繼續上修，否則不適合追高。"
        if request.industry_cycle == "cyclical":
            return "適合分批與紀律型操作，不適合完全無腦長抱或一路攤平。"
        if request.industry_cycle == "stable":
            return "若基本面穩定且估值合理，可用較高比例做長期投入。"
        return "資料不足時先採保守分批，等財報、營收與估值資料補齊後再提高投入比例。"

    def _price_bands(self, request: InvestmentPlanRequest) -> list[PriceBand]:
        price = request.current_price
        return [
            PriceBand(name="正常投入區", price=_round_money(price * 0.95), action="股價低於此區且基本面未轉弱，可按計畫投入。"),
            PriceBand(name="加碼觀察區", price=_round_money(price * 0.85), action="回檔達此區可使用部分保留現金，但需確認營收與獲利沒有惡化。"),
            PriceBand(name="深度加碼區", price=_round_money(price * 0.75), action="僅在長期假設仍成立時加碼，單股部位不得超過上限。"),
            PriceBand(name="暫停追價區", price=_round_money(price * 1.1), action="短線漲幅過大時暫停新增投入，等待月營收或財報驗證。"),
        ]

    def _action_rules(self, request: InvestmentPlanRequest, allocation: AllocationPlan) -> list[ActionRule]:
        rules = [
            ActionRule(trigger="每月固定檢查日", action="依固定投入比例買進，剩餘金額留作保留現金。"),
            ActionRule(trigger=f"帳面虧損達 {request.max_loss_percent:.0f}%", action="暫停自動投入，重新檢查基本面與產業循環，不直接攤平。"),
            ActionRule(trigger=f"帳面報酬達 {request.target_return_percent:.0f}%", action="至少分批停利 20%-30%，保留核心部位續抱。"),
            ActionRule(trigger="連續兩期財報或月營收轉弱", action="降低固定投入比例，並把計畫狀態改為觀察。"),
        ]
        if request.plan_type == "recurring_investment" and allocation.monthly_fixed_buy_amount is not None:
            rules.insert(
                0,
                ActionRule(
                    trigger="每月預算入帳",
                    action=(
                        f"投入 {allocation.monthly_fixed_buy_amount:,.0f} 元買進零股或整股，"
                        f"保留 {allocation.monthly_cash_reserve_amount:,.0f} 元等待回檔。"
                    ),
                ),
            )
        return rules

    def _tracking_indicators(self, request: InvestmentPlanRequest) -> list[str]:
        indicators = ["股價與平均成本", "月營收年增率", "EPS 與毛利率", "本益比/股價淨值比", "法人或大戶籌碼變化"]
        if request.industry_cycle == "cyclical":
            indicators.insert(2, "產業報價與庫存循環")
        if request.tracked_materials:
            indicators.append(f"原料/成本追蹤：{', '.join(request.tracked_materials[:8])}")
        if request.public_event_keywords:
            indicators.append(f"公開事件追蹤：{', '.join(request.public_event_keywords[:8])}")
        return indicators

    def _risk_notes(self, request: InvestmentPlanRequest) -> list[str]:
        notes = ["單一股票不宜占整體投資資產過高，避免個股事件造成資產大幅波動。"]
        if request.industry_cycle == "cyclical":
            notes.append("景氣循環股在獲利高峰時估值可能看似便宜，需避免用高峰 EPS 推估永久獲利。")
        if request.valuation_level in {"expensive", "overheated"}:
            notes.append("估值偏高時，定期投入比例應降低，優先保留現金等待風險報酬改善。")
        if not request.allow_high_volatility:
            notes.append("會員不接受高波動時，系統應提高現金保留並降低加碼速度。")
        if request.public_event_keywords:
            notes.append("企業高層行程只能追蹤公開來源，不追蹤私人行程；未經證實消息不得作為買賣依據。")
        return notes

    def _next_review_items(self, request: InvestmentPlanRequest) -> list[str]:
        return [
            "補上最近一季財報與近 12 個月營收趨勢。",
            "確認目前股價落在哪個買進區間。",
            "檢查會員實際持股成本是否偏離原計畫。",
            "每月重新計算固定投入與保留現金比例。",
        ]

    def _projection(self, request: InvestmentPlanRequest, allocation: AllocationPlan) -> InvestmentProjection:
        estimated_monthly_shares = allocation.estimated_monthly_shares
        estimated_total_shares = None
        if estimated_monthly_shares is not None:
            estimated_total_shares = round(estimated_monthly_shares * 12 * request.investment_years, 4)
        cost_basis = request.average_cost or request.current_price
        return InvestmentProjection(
            invested_principal=allocation.total_planned_contribution,
            estimated_shares_from_monthly_plan=estimated_total_shares,
            break_even_price=_round_money(cost_basis),
            target_take_profit_price=_round_money(cost_basis * (1 + request.target_return_percent / 100)),
            max_loss_review_price=_round_money(cost_basis * (1 - request.max_loss_percent / 100)),
        )

    def build_review(self, plan: InvestmentPlanRecord, request: PlanReviewRequest) -> PlanReviewRecord:
        cost_basis = request.average_cost or plan.request.average_cost or plan.request.current_price
        target_price = cost_basis * (1 + plan.request.target_return_percent / 100)
        risk_review_price = cost_basis * (1 - plan.request.max_loss_percent / 100)
        recommendation = "hold"
        risk_flags: list[str] = []
        next_actions: list[str] = []

        if request.current_price >= target_price:
            recommendation = "take_profit"
            next_actions.append("達到目標報酬區，優先分批停利 20%-30%。")
        elif request.current_price <= risk_review_price:
            recommendation = "risk_review"
            risk_flags.append("股價已跌到最大虧損檢查區。")
            next_actions.append("暫停自動投入，重新檢查基本面與原始投資假設。")
        elif request.revenue_trend == "weakening" or request.earnings_trend == "weakening":
            recommendation = "pause"
            risk_flags.append("營收或獲利趨勢轉弱。")
            next_actions.append("本月先不加碼，等待下一期數據確認。")
        elif request.material_cost_trend == "weakening":
            recommendation = "pause"
            risk_flags.append("原料或成本趨勢惡化。")
            next_actions.append("先確認毛利率是否承壓，再決定是否恢復投入。")
        elif request.valuation_level == "overheated":
            recommendation = "pause"
            risk_flags.append("估值狀態偏過熱。")
            next_actions.append("保留本月預算，等待估值或股價回到合理區。")
        elif request.global_peer_trend == "weakening":
            recommendation = "hold"
            risk_flags.append("全球同業股價或產業氣氛轉弱。")
            next_actions.append("降低追價意願，等待同業財報或報價趨勢確認。")
        elif request.public_event_signal == "improving":
            recommendation = "hold"
            next_actions.append("有正向公開事件，可納入觀察，但仍需等待營收與財報驗證。")
        elif request.current_price <= plan.price_bands[2].price and request.revenue_trend in {"improving", "stable"}:
            recommendation = "add"
            next_actions.append("可用部分保留現金加碼，但仍要守住單股部位上限。")
        elif request.current_price <= plan.price_bands[0].price:
            recommendation = "buy"
            next_actions.append("可依原定固定投入比例執行本月買進。")
        else:
            next_actions.append("本月不追價，保留現金並等待下一次檢查。")

        action_amount = self._review_action_amount(plan, request, recommendation)
        cash_after_action = max(0.0, request.available_cash - action_amount)
        estimated_shares = round(action_amount / request.current_price, 4) if action_amount else None
        if not risk_flags:
            risk_flags.append("未觸發重大風險旗標。")

        return PlanReviewRecord(
            id=uuid4(),
            plan_id=plan.id,
            created_at=_now(),
            request=request,
            recommendation=recommendation,
            recommendation_label=self._recommendation_label(recommendation),
            summary=self._review_summary(plan, request, recommendation, action_amount),
            suggested_action_amount=_round_money(action_amount),
            suggested_cash_reserve=_round_money(cash_after_action),
            estimated_shares_to_buy=estimated_shares,
            price_position=self._price_position(plan, request.current_price),
            risk_flags=risk_flags,
            next_actions=next_actions,
        )

    def _review_action_amount(
        self,
        plan: InvestmentPlanRecord,
        request: PlanReviewRequest,
        recommendation: str,
    ) -> float:
        monthly_fixed = plan.allocation.monthly_fixed_buy_amount or 0
        if recommendation == "buy":
            return min(request.available_cash, monthly_fixed)
        if recommendation == "add":
            reserve_boost = (plan.allocation.monthly_cash_reserve_amount or 0) * 0.5
            return min(request.available_cash, monthly_fixed + reserve_boost)
        return 0.0

    def _recommendation_label(self, recommendation: str) -> str:
        labels = {
            "buy": "本月照計畫買進",
            "add": "本月可小幅加碼",
            "hold": "本月觀察保留現金",
            "pause": "本月暫停投入",
            "take_profit": "觸發分批停利",
            "risk_review": "觸發風險檢查",
        }
        return labels[recommendation]

    def _review_summary(
        self,
        plan: InvestmentPlanRecord,
        request: PlanReviewRequest,
        recommendation: str,
        action_amount: float,
    ) -> str:
        stock = plan.request.stock_name or plan.request.stock_symbol
        if recommendation in {"buy", "add"}:
            return f"{stock} 本月建議投入約 {action_amount:,.0f} 元，剩餘現金保留給下一次回檔或風險檢查。"
        if recommendation == "take_profit":
            return f"{stock} 已達原計畫停利參考區，建議先分批實現部分獲利。"
        if recommendation == "risk_review":
            return f"{stock} 已跌到風險檢查區，本月重點不是攤平，而是確認基本面是否仍成立。"
        if recommendation == "pause":
            return f"{stock} 本月條件不適合新增投入，建議保留現金等待下一次數據更新。"
        return f"{stock} 本月沒有明確買進訊號，維持觀察並保留現金。"

    def _price_position(self, plan: InvestmentPlanRecord, current_price: float) -> str:
        if current_price >= plan.price_bands[3].price:
            return "暫停追價區"
        if current_price <= plan.price_bands[2].price:
            return "深度加碼區"
        if current_price <= plan.price_bands[1].price:
            return "加碼觀察區"
        if current_price <= plan.price_bands[0].price:
            return "正常投入區"
        return "觀察區"


class InvestmentPlanStore:
    def __init__(self, path: str | Path = "data/investment_plans_store.json") -> None:
        self.path = Path(path)
        self.engine = InvestmentPlanEngine()

    def create(self, request: InvestmentPlanRequest) -> InvestmentPlanRecord:
        plan = self.engine.build_plan(request)
        plans = self._load_plans()
        plans.append(plan)
        self._save(plans, self._load_reviews(), self._load_subscriptions())
        return plan

    def list_for_user(self, user_id: str | None = None) -> list[InvestmentPlanRecord]:
        plans = self._load_plans()
        if user_id:
            plans = [plan for plan in plans if plan.request.user_id == user_id]
        return sorted(plans, key=lambda item: item.created_at, reverse=True)

    def get(self, plan_id: UUID) -> InvestmentPlanRecord:
        for plan in self._load_plans():
            if plan.id == plan_id:
                return plan
        raise KeyError(str(plan_id))

    def create_review(self, plan_id: UUID, request: PlanReviewRequest) -> PlanReviewRecord:
        plan = self.get(plan_id)
        review = self.engine.build_review(plan, request)
        reviews = self._load_reviews()
        reviews.append(review)
        self._save(self._load_plans(), reviews, self._load_subscriptions())
        return review

    def list_reviews(self, plan_id: UUID) -> list[PlanReviewRecord]:
        reviews = [review for review in self._load_reviews() if review.plan_id == plan_id]
        return sorted(reviews, key=lambda item: item.created_at, reverse=True)

    def latest_review(self, plan_id: UUID) -> PlanReviewRecord | None:
        reviews = self.list_reviews(plan_id)
        return reviews[0] if reviews else None

    def create_line_subscription(self, request: LineSubscriptionRequest) -> LineSubscriptionRecord:
        if request.plan_id:
            self.get(request.plan_id)
        timestamp = _now()
        subscription = LineSubscriptionRecord(
            id=uuid4(),
            created_at=timestamp,
            updated_at=timestamp,
            user_id=request.user_id,
            line_user_id=request.line_user_id,
            plan_id=request.plan_id,
            frequency=request.frequency,
            consent=request.consent,
            status="active" if request.line_user_id and request.consent else "pending_binding",
        )
        subscriptions = [
            item
            for item in self._load_subscriptions()
            if not (item.user_id == subscription.user_id and item.plan_id == subscription.plan_id)
        ]
        subscriptions.append(subscription)
        self._save(self._load_plans(), self._load_reviews(), subscriptions)
        return subscription

    def list_line_subscriptions(self, user_id: str | None = None) -> list[LineSubscriptionRecord]:
        subscriptions = self._load_subscriptions()
        if user_id:
            subscriptions = [item for item in subscriptions if item.user_id == user_id]
        return sorted(subscriptions, key=lambda item: item.created_at, reverse=True)

    def _read_payload(self) -> dict[str, object]:
        if not self.path.exists():
            return {"plans": [], "reviews": [], "line_subscriptions": []}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"plans": [], "reviews": [], "line_subscriptions": []}

    def _load_plans(self) -> list[InvestmentPlanRecord]:
        raw = self._read_payload()
        return [InvestmentPlanRecord.model_validate(item) for item in raw.get("plans", [])]

    def _load_reviews(self) -> list[PlanReviewRecord]:
        raw = self._read_payload()
        return [PlanReviewRecord.model_validate(item) for item in raw.get("reviews", [])]

    def _load_subscriptions(self) -> list[LineSubscriptionRecord]:
        raw = self._read_payload()
        return [LineSubscriptionRecord.model_validate(item) for item in raw.get("line_subscriptions", [])]

    def _save(
        self,
        plans: list[InvestmentPlanRecord],
        reviews: list[PlanReviewRecord],
        subscriptions: list[LineSubscriptionRecord],
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "plans": [plan.model_dump(mode="json") for plan in plans],
            "reviews": [review.model_dump(mode="json") for review in reviews],
            "line_subscriptions": [subscription.model_dump(mode="json") for subscription in subscriptions],
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
