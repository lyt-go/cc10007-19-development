"""价值驱动渠道预算分配业务规则。

数据链路：
    LTV模型用户价值记录（ltv_value_record，按首次接入读取）
        + 预算渠道配置（budget_channel_config）
        -> 渠道预算分配明细数据集（channel_budget_batch / channel_budget_line）

关键约束（对应核验码 1924d3b6 业务）：
- 预算总额、渠道比例、LTV版本在同一次写入里落库，任何一项不完整都不产生正式数据集；
- 估算未完成 / 未发布模型的用户价值只能进入候选分配，不能进入正式分配；
- 同一渠道存在两个可用 LTV 版本时，按「观察窗口更完整，其次最近发布」裁决，
  预算方案只引用一个当前价值版本，另一版本保留为候选依据；
- 并发提交靠进程锁 + 幂等键保证服务端只写入一个完整结果；
- 请求失败重试只续写仍处于待写入的额度，已写入额度不重复落账；
- 重复登记收敛为同一个已闭环明细数据集，不重复建单。
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from app.store import store

BIZ_CODE = "1924d3b6"

VALUE_MODULE = "ltv_value_record"
CONFIG_MODULE = "budget_channel_config"
BATCH_MODULE = "channel_budget_batch"
LINE_MODULE = "channel_budget_line"
LIFETIME_MODULE = "lifetime"

VALUE_DONE = "已完成"
MODEL_PUBLISHED = "已发布"
LINE_PENDING = "待写入"
LINE_WRITTEN = "已写入"
BATCH_WAITING = "待处理"
BATCH_PROCESSING = "处理中"
BATCH_CLOSED = "已闭环"

# 渠道额度 = 50% 配置比例权重 + 50% LTV 用户价值占比权重
CONFIG_WEIGHT = 0.5
VALUE_WEIGHT = 0.5


class ChannelBudgetError(Exception):
    """业务校验失败：消息直接回给前端，不吞错。"""


class ChannelBudgetService:
    def __init__(self) -> None:
        # 内存仓库下的进程锁：保证并发提交时临界区只进一个线程
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ 读侧
    def list_value_records(
        self,
        *,
        channel: str | None = None,
        model_no: str | None = None,
        estimate_status: str | None = None,
        keyword: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = [dict(row) for row in store.rows(VALUE_MODULE)]
        if channel:
            rows = [row for row in rows if row.get("渠道") == channel]
        if model_no:
            rows = [row for row in rows if row.get("模型编号") == model_no]
        if estimate_status:
            rows = [row for row in rows if row.get("估算状态") == estimate_status]
        if keyword:
            rows = [
                row
                for row in rows
                if keyword in str(row.get("价值记录编号", ""))
                or keyword in str(row.get("渠道", ""))
                or keyword in str(row.get("模型编号", ""))
            ]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def list_channel_configs(self) -> list[dict[str, Any]]:
        return [dict(row) for row in store.rows(CONFIG_MODULE)]

    def list_batches(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(BATCH_MODULE)
        if keyword:
            rows = [
                row
                for row in rows
                if keyword in str(row.get("批次编号", ""))
                or keyword in str(row.get("批次资料号", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("状态") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_batch(self, batch_no: str) -> dict[str, Any] | None:
        for row in store.rows(BATCH_MODULE):
            if row.get("批次编号") == batch_no:
                return row
        return None

    def list_lines(self, batch_no: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in store.rows(LINE_MODULE)
            if row.get("批次编号") == batch_no
        ]

    # ------------------------------------------------------------ 版本裁决
    def _pick_value_record(self, records: list[dict[str, Any]]) -> dict[str, Any] | None:
        """同一渠道多条记录时选当前价值版本。

        裁决顺序：观察窗口更完整（观察窗口天更大）优先；观察窗口相同则最近发布优先。
        只有「估算已完成 + 模型已发布」的记录有资格参与正式分配。
        """
        eligible = [
            row
            for row in records
            if row.get("估算状态") == VALUE_DONE and row.get("发布状态") == MODEL_PUBLISHED
        ]
        if not eligible:
            return None
        return max(
            eligible,
            key=lambda row: (int(row.get("观察窗口天", 0)), str(row.get("发布时间", ""))),
        )

    def _channel_decision(self, channel: str, records: list[dict[str, Any]]) -> dict[str, Any]:
        """汇总单个渠道的版本裁决结果，含正式依据与候选依据。"""
        chosen = self._pick_value_record(records)
        candidates: list[dict[str, Any]] = []
        for row in records:
            if chosen is not None and row.get("id") == chosen.get("id"):
                continue
            reasons: list[str] = []
            if row.get("估算状态") != VALUE_DONE:
                reasons.append("估算未完成，只能进入候选分配")
            if row.get("发布状态") != MODEL_PUBLISHED:
                reasons.append("LTV模型未发布，只能进入候选分配")
            if not reasons and chosen is not None:
                if int(row.get("观察窗口天", 0)) < int(chosen.get("观察窗口天", 0)):
                    reasons.append("观察窗口不如当前版本完整")
                elif str(row.get("发布时间", "")) < str(chosen.get("发布时间", "")):
                    reasons.append("发布时间早于当前版本")
                else:
                    reasons.append("同一渠道仅允许引用一个当前价值版本")
            candidates.append(
                {
                    "价值记录编号": row.get("价值记录编号"),
                    "模型编号": row.get("模型编号"),
                    "模型版本": row.get("模型版本"),
                    "观察窗口天": row.get("观察窗口天"),
                    "发布状态": row.get("发布状态"),
                    "估算状态": row.get("估算状态"),
                    "候选原因": "；".join(reasons) or "候选价值依据",
                }
            )
        choose_reason = ""
        if chosen is not None:
            longer = [
                row
                for row in records
                if row.get("id") != chosen.get("id")
                and row.get("估算状态") == VALUE_DONE
                and row.get("发布状态") == MODEL_PUBLISHED
            ]
            if longer and all(
                int(chosen.get("观察窗口天", 0)) > int(row.get("观察窗口天", 0)) for row in longer
            ):
                choose_reason = "观察窗口更完整"
            elif longer:
                choose_reason = "观察窗口相同，采用最近发布的模型"
            else:
                choose_reason = "渠道唯一可用价值版本"
        return {
            "渠道": channel,
            "正式记录": chosen,
            "候选依据": candidates,
            "采用原因": choose_reason,
        }

    # ------------------------------------------------------------ 核对/试算
    def validate_selection(
        self, record_ids: list[int], total_budget: float
    ) -> dict[str, Any]:
        """逐条核对多选记录并整组试算，不写库。"""
        if not record_ids:
            raise ChannelBudgetError("请至少勾选一条LTV用户价值记录")
        if total_budget <= 0:
            raise ChannelBudgetError("预算总额必须为正数")

        all_records = store.rows(VALUE_MODULE)
        selected: list[dict[str, Any]] = []
        record_checks: list[dict[str, Any]] = []
        for record_id in record_ids:
            row = next((item for item in all_records if int(item.get("id", 0)) == record_id), None)
            if row is None:
                record_checks.append(
                    {"价值记录编号": f"#{record_id}", "通过": False, "说明": "用户价值记录不存在"}
                )
                continue
            selected.append(row)
            problems: list[str] = []
            if row.get("估算状态") != VALUE_DONE:
                problems.append("估算未完成，不能进入正式分配")
            if row.get("发布状态") != MODEL_PUBLISHED:
                problems.append("LTV模型未发布，只能进入候选分配")
            record_checks.append(
                {
                    "价值记录编号": row.get("价值记录编号"),
                    "渠道": row.get("渠道"),
                    "模型编号": row.get("模型编号"),
                    "模型版本": row.get("模型版本"),
                    "估算状态": row.get("估算状态"),
                    "发布状态": row.get("发布状态"),
                    "通过": not problems,
                    "说明": "核对通过" if not problems else "；".join(problems),
                }
            )

        configs = self.list_channel_configs()
        config_map = {row["渠道"]: row for row in configs}
        ratio_sum = round(sum(float(row["渠道比例"]) for row in configs), 6)
        ratio_ok = abs(ratio_sum - 1.0) < 1e-6

        blocked = [item for item in record_checks if not item["通过"]]
        decisions: list[dict[str, Any]] = []
        channels = sorted({row["渠道"] for row in selected})
        for channel in channels:
            decision = self._channel_decision(
                channel, [row for row in selected if row.get("渠道") == channel]
            )
            decision["渠道配置"] = config_map.get(channel)
            if channel not in config_map:
                decision["配置问题"] = "缺少预算渠道配置，无法承接该渠道额度"
            decisions.append(decision)

        missing_channels = [
            row["渠道"] for row in configs if row["渠道"] not in channels
        ]
        can_submit = (
            not blocked
            and ratio_ok
            and not missing_channels
            and all(item["正式记录"] is not None for item in decisions)
            and all("配置问题" not in item for item in decisions)
        )

        preview_lines: list[dict[str, Any]] = []
        if can_submit:
            preview_lines = self._build_lines(decisions, config_map, total_budget)

        return {
            "业务核验码": BIZ_CODE,
            "核对通过": can_submit,
            "逐条核对": record_checks,
            "渠道比例核对": {"比例合计": ratio_sum, "通过": ratio_ok},
            "缺失渠道": missing_channels,
            "版本裁决": [
                {
                    "渠道": item["渠道"],
                    "采用版本": (
                        f"{item['正式记录']['模型编号']} {item['正式记录']['模型版本']}"
                        if item["正式记录"]
                        else None
                    ),
                    "采用原因": item["采用原因"],
                    "候选依据": item["候选依据"],
                    "配置问题": item.get("配置问题"),
                }
                for item in decisions
            ],
            "试算明细": preview_lines,
        }

    # ------------------------------------------------------------ 额度计算
    def _build_lines(
        self,
        decisions: list[dict[str, Any]],
        config_map: dict[str, dict[str, Any]],
        total_budget: float,
    ) -> list[dict[str, Any]]:
        """根据渠道配置比例与 LTV 用户价值占比计算渠道额度、ROI偏差、消耗速率。"""
        total_value = sum(
            float(item["正式记录"]["用户价值合计"]) for item in decisions
        )
        if total_value <= 0:
            raise ChannelBudgetError("入选用户价值合计为 0，无法驱动渠道分配")

        raw_weights: list[tuple[str, float]] = []
        for item in decisions:
            channel = item["渠道"]
            record = item["正式记录"]
            config = config_map[channel]
            value_share = float(record["用户价值合计"]) / total_value
            weight = (
                CONFIG_WEIGHT * float(config["渠道比例"])
                + VALUE_WEIGHT * value_share
            )
            raw_weights.append((channel, weight))
        weight_sum = sum(weight for _, weight in raw_weights)

        lines: list[dict[str, Any]] = []
        allocated = 0.0
        for index, (channel, weight) in enumerate(raw_weights):
            record = next(item["正式记录"] for item in decisions if item["渠道"] == channel)
            config = config_map[channel]
            normalized_weight = weight / weight_sum
            if index == len(raw_weights) - 1:
                # 尾差收口到最后一个渠道，保证渠道额度之和严格等于预算总额
                quota = round(total_budget - allocated, 2)
            else:
                quota = round(total_budget * normalized_weight, 2)
                allocated += quota
            value_total = float(record["用户价值合计"])
            expected_roi = round(value_total / quota, 4) if quota else 0.0
            roi_target = float(config["ROI目标"])
            roi_gap = round(expected_roi - roi_target, 4)
            window_days = int(record["预测窗口天"])
            burn_rate = round(quota / window_days, 2)
            daily_cap = float(config["日消耗上限"])
            lines.append(
                {
                    "渠道": channel,
                    "渠道额度": quota,
                    "配置渠道比例": float(config["渠道比例"]),
                    "实际分配比例": round(quota / total_budget, 6),
                    "LTV模型编号": record["模型编号"],
                    "LTV版本": f"{record['模型编号']} {record['模型版本']}",
                    "价值记录编号": record["价值记录编号"],
                    "观察窗口天": int(record["观察窗口天"]),
                    "预测窗口天": window_days,
                    "用户价值合计": value_total,
                    "ROI目标": roi_target,
                    "预计ROI": expected_roi,
                    "ROI目标偏差": roi_gap,
                    "ROI目标偏差率": round(roi_gap / roi_target, 4) if roi_target else 0.0,
                    "预计消耗速率": burn_rate,
                    "日消耗上限": daily_cap,
                    "速率是否超限": burn_rate > daily_cap,
                    "写入状态": LINE_PENDING,
                }
            )
        return lines

    # ------------------------------------------------------------ 写入/并发
    def _next_batch_no(self) -> str:
        existing = store.rows(BATCH_MODULE)
        today = datetime.now().strftime("%Y%m%d")
        seq = (
            sum(1 for row in existing if str(row.get("批次编号", "")).startswith(f"CBA-{today}"))
            + 1
        )
        return f"CBA-{today}-{seq:03d}"

    @staticmethod
    def _default_idem_key(record_ids: list[int], total_budget: float) -> str:
        joined = ",".join(str(i) for i in sorted(record_ids))
        return f"{joined}|{round(total_budget, 2)}"

    def create_batch(
        self,
        record_ids: list[int],
        total_budget: float,
        *,
        idem_key: str | None = None,
        operator: str = "值班管理员",
        simulate_failure: bool = False,
    ) -> dict[str, Any]:
        """整组提交：服务端在同一写入里落 预算总额+渠道比例+LTV版本+明细行。"""
        idem_key = idem_key or self._default_idem_key(record_ids, total_budget)
        with self._lock:
            # 并发/重复提交：幂等键命中则直接收敛到既有数据集，不再新建
            for existing in store.rows(BATCH_MODULE):
                if existing.get("幂等键") == idem_key:
                    return {
                        "converged": True,
                        "batch": existing,
                        "message": "重复登记已收敛到既有渠道预算分配明细数据集",
                    }

            validation = self.validate_selection(record_ids, total_budget)
            if not validation["核对通过"]:
                problems = [
                    item["说明"] for item in validation["逐条核对"] if not item["通过"]
                ]
                if validation["缺失渠道"]:
                    problems.append(
                        f"缺少渠道覆盖：{'、'.join(validation['缺失渠道'])}"
                    )
                if not validation["渠道比例核对"]["通过"]:
                    problems.append("渠道比例合计不为 1，配置校验未通过")
                raise ChannelBudgetError("；".join(problems) or "核对未通过，拒绝正式分配")

            decisions = []
            all_records = store.rows(VALUE_MODULE)
            config_map = {row["渠道"]: row for row in self.list_channel_configs()}
            for channel in sorted({row["渠道"] for row in all_records if int(row.get("id", 0)) in record_ids}):
                decision = self._channel_decision(
                    channel,
                    [
                        row
                        for row in all_records
                        if int(row.get("id", 0)) in record_ids and row.get("渠道") == channel
                    ],
                )
                decision["渠道配置"] = config_map[channel]
                decisions.append(decision)

            lines = self._build_lines(decisions, config_map, total_budget)
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            batch_no = self._next_batch_no()
            batch = {
                "id": len(store.rows(BATCH_MODULE)) + 1,
                "批次编号": batch_no,
                "批次资料号": f"BMAT-{batch_no[4:]}",
                "业务核验码": BIZ_CODE,
                "状态": BATCH_WAITING,
                "预算总额": round(total_budget, 2),
                # 三项关键字段随同一写入落库
                "渠道比例": {line["渠道"]: line["配置渠道比例"] for line in lines},
                "LTV版本": {line["渠道"]: line["LTV版本"] for line in lines},
                "渠道额度": {line["渠道"]: line["渠道额度"] for line in lines},
                "版本裁决": validation["版本裁决"],
                "幂等键": idem_key,
                "操作人": operator,
                "提交时间": now,
                "待写入额度数": len(lines),
                "已写入额度数": 0,
                "闭环时间": None,
                "回写动作": None,
            }
            store.rows(BATCH_MODULE).append(batch)
            for index, line in enumerate(lines, start=1):
                row = {"id": len(store.rows(LINE_MODULE)) + 1, "批次编号": batch_no, "行号": index}
                row.update(line)
                store.rows(LINE_MODULE).append(row)

        # 临界区外逐行落账：失败后重试只续写剩余待写入额度
        processed = self._write_pending_lines(batch_no, max_lines=2 if simulate_failure else None)
        fresh = self.get_batch(batch_no)
        return {
            "converged": False,
            "batch": fresh,
            "written": processed,
            "message": (
                "模拟失败：仅前两行落账，请调用续写接口完成剩余额度"
                if simulate_failure
                else "渠道预算分配明细数据集已保存，等待处理闭环"
            ),
        }

    def _write_pending_lines(self, batch_no: str, max_lines: int | None = None) -> int:
        batch = self.get_batch(batch_no)
        if batch is None:
            raise ChannelBudgetError(f"批次 {batch_no} 不存在")
        if batch["状态"] == BATCH_CLOSED:
            return 0
        lines = [
            row
            for row in store.rows(LINE_MODULE)
            if row.get("批次编号") == batch_no and row.get("写入状态") == LINE_PENDING
        ]
        if max_lines is not None:
            lines = lines[:max_lines]
        for line in lines:
            line["写入状态"] = LINE_WRITTEN
            line["落账时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        all_lines = self.list_lines(batch_no)
        written = sum(1 for row in all_lines if row["写入状态"] == LINE_WRITTEN)
        pending = sum(1 for row in all_lines if row["写入状态"] == LINE_PENDING)
        batch["已写入额度数"] = written
        batch["待写入额度数"] = pending
        if pending == 0 and batch["状态"] == BATCH_WAITING:
            batch["状态"] = BATCH_PROCESSING
        return len(lines)

    def continue_batch(self, batch_no: str) -> dict[str, Any]:
        """请求失败后的重试：只续写未完成额度，已写入额度不重复落账。"""
        batch = self.get_batch(batch_no)
        if batch is None:
            raise ChannelBudgetError(f"批次 {batch_no} 不存在或已归档")
        if batch["状态"] == BATCH_CLOSED:
            return {"batch": batch, "continued": 0, "message": "数据集已闭环，无需续写"}
        with self._lock:
            continued = self._write_pending_lines(batch_no)
        return {
            "batch": self.get_batch(batch_no),
            "continued": continued,
            "message": f"已续写 {continued} 条未完成额度" if continued else "没有待写入额度",
        }

    def close_batch(self, batch_no: str) -> dict[str, Any]:
        """闭环：全部额度落账后收敛为单个已闭环数据集，并回写 LTV 估算列表。"""
        batch = self.get_batch(batch_no)
        if batch is None:
            raise ChannelBudgetError(f"批次 {batch_no} 不存在或已归档")
        with self._lock:
            if batch["状态"] == BATCH_CLOSED:
                return {"batch": batch, "message": "数据集已闭环，重复操作已收敛"}
            pending = batch.get("待写入额度数", 0)
            if pending:
                raise ChannelBudgetError(
                    f"仍有 {pending} 条渠道额度未写入，请先续写再闭环"
                )
            batch["状态"] = BATCH_CLOSED
            batch["闭环时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # 回写 LTV 估算列表：LIFE-0003 为回归样例，动作文本固定
            referenced = sorted({line["LTV模型编号"] for line in self.list_lines(batch_no)})
            for model_no in referenced:
                target = next(
                    (
                        row
                        for row in store.rows(LIFETIME_MODULE)
                        if row.get("模型编号") == model_no
                    ),
                    None,
                )
                if target is None:
                    continue
                action_label = (
                    f"预算分配（{model_no}）"
                    if model_no != "LIFE-0003"
                    else "预算分配（LIFE-0003）"
                )
                target["预算分配动作"] = action_label
                target["预算分配批次"] = batch_no
                target["预算分配回写时间"] = batch["闭环时间"]
                target["预算分配状态"] = BATCH_CLOSED
            batch["回写动作"] = [f"预算分配（{model_no}）" for model_no in referenced]
        return {"batch": self.get_batch(batch_no), "message": "渠道预算分配明细数据集已闭环并回写LTV估算列表"}


service = ChannelBudgetService()
