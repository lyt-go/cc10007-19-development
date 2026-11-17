"""价值驱动渠道预算分配业务规则回归脚本（不依赖 pytest，直接 python 运行）。

覆盖：
- 版本裁决（同渠道两个可用 LTV 版本 -> 观察窗口更完整者胜出）
- 未完成估算 / 未发布模型只能进候选
- 预算总额、渠道比例、LTV版本同一写入；额度合计严格等于预算总额
- 重复登记幂等收敛、并发提交只写入一个完整结果
- 失败重试只续写未完成额度；闭环后回写 LTV 估算列表（LIFE-0003 回归样例）
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.channel_budget import (  # noqa: E402
    BATCH_CLOSED,
    BATCH_PROCESSING,
    BATCH_WAITING,
    LINE_PENDING,
    LINE_WRITTEN,
    ChannelBudgetError,
    ChannelBudgetService,
)
from app.store import store  # noqa: E402

FORMAL_RECORD_IDS = [1, 2, 3, 4, 5, 7, 8, 9, 10, 11]
TOTAL_BUDGET = 1_000_000.0
failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f" -> {detail}" if detail else ""))
    if not condition:
        failures.append(name)


def main() -> int:
    svc = ChannelBudgetService()

    # 1. 版本裁决：信息流同时有 LIFE-0003(窗口90) 与 LIFE-0004(窗口120)
    decision = svc._channel_decision(
        "信息流",
        [row for row in store.rows("ltv_value_record") if row["渠道"] == "信息流"],
    )
    chosen = decision["正式记录"]
    check("同渠道双版本裁决到观察窗口更完整的 LIFE-0004", chosen["模型编号"] == "LIFE-0004", chosen["模型版本"])
    check("裁决原因标注为观察窗口更完整", decision["采用原因"] == "观察窗口更完整", decision["采用原因"])
    check("落选版本保留为候选依据", any(c["模型编号"] == "LIFE-0003" for c in decision["候选依据"]))

    # 2. 未发布/估算未完成记录不能通过核对
    blocked = svc.validate_selection([6], TOTAL_BUDGET)
    check("未发布 LTV 模型核对不通过", not blocked["核对通过"])
    check("未发布记录提示只能进入候选分配", any("未发布" in c["说明"] for c in blocked["逐条核对"]))
    blocked2 = svc.validate_selection([12], TOTAL_BUDGET)
    check("估算未完成记录核对不通过", not blocked2["核对通过"])
    check("估算未完成记录提示不能进入正式分配", any("估算未完成" in c["说明"] for c in blocked2["逐条核对"]))

    # 3. 整组核对与试算
    validation = svc.validate_selection(FORMAL_RECORD_IDS, TOTAL_BUDGET)
    check("正式记录整组核对通过", validation["核对通过"])
    check("渠道比例合计为 1", validation["渠道比例核对"]["通过"], str(validation["渠道比例核对"]["比例合计"]))
    preview = validation["试算明细"]
    check("试算覆盖全部 5 个渠道", len(preview) == 5, str(len(preview)))
    quota_sum = round(sum(line["渠道额度"] for line in preview), 2)
    check("试算额度合计严格等于预算总额", quota_sum == TOTAL_BUDGET, str(quota_sum))
    check("每条试算都携带 LTV 版本", all(line["LTV版本"].startswith("LIFE-") for line in preview))
    # 规则是「同一渠道只引用一个当前价值版本」：双版本渠道统一裁决到 LIFE-0004，
    # 搜索引擎仅有 LIFE-0003 可用（LIFE-0005 未发布），保留 LIFE-0003
    version_by_channel = {line["渠道"]: line["LTV模型编号"] for line in preview}
    check("双版本渠道裁决到 LIFE-0004",
          all(version_by_channel[ch] == "LIFE-0004" for ch in ("信息流", "社交裂变", "电商联盟", "私域社群")),
          str(version_by_channel))
    check("单版本渠道保留唯一可用 LIFE-0003", version_by_channel["搜索引擎"] == "LIFE-0003")
    check("试算输出 ROI 目标偏差与预计消耗速率", all("ROI目标偏差" in line and "预计消耗速率" in line for line in preview))

    # 4. 正式提交：同一写入携带 预算总额+渠道比例+LTV版本
    result = svc.create_batch(FORMAL_RECORD_IDS, TOTAL_BUDGET, idem_key="regression-key-1")
    batch = result["batch"]
    batch_no = batch["批次编号"]
    check("提交后携带当前批次资料", bool(batch["批次资料号"]) and batch["业务核验码"] == "1924d3b6")
    check("同一写入落预算总额", batch["预算总额"] == TOTAL_BUDGET)
    check("同一写入落渠道比例", set(batch["渠道比例"]) == {line["渠道"] for line in preview})
    check("同一写入落 LTV 版本", all(v.startswith("LIFE-") for v in batch["LTV版本"].values()))
    lines = svc.list_lines(batch_no)
    check("全部渠道额度一次性落账", all(line["写入状态"] == LINE_WRITTEN for line in lines))
    check("批次进入处理中（等待闭环）", batch["状态"] == BATCH_PROCESSING, batch["状态"])

    # 5. 重复登记收敛为同一个数据集
    repeat = svc.create_batch(FORMAL_RECORD_IDS, TOTAL_BUDGET, idem_key="regression-key-1")
    check("重复登记命中幂等收敛", repeat["converged"] and repeat["batch"]["批次编号"] == batch_no)
    check("收敛后批次总数不增加", len(store.rows("channel_budget_batch")) == 1)

    # 6. 并发提交：两个线程同参数同时提交，服务端只写入一个完整结果
    barrier = threading.Barrier(2)
    outcomes: list[dict] = []

    def worker() -> None:
        barrier.wait()
        outcomes.append(svc.create_batch(FORMAL_RECORD_IDS, TOTAL_BUDGET, idem_key="concurrent-key"))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    batch_nos = {item["batch"]["批次编号"] for item in outcomes}
    check("并发提交只产生一个批次", len(batch_nos) == 1, str(batch_nos))
    check("并发后其中一次为收敛返回", any(item["converged"] for item in outcomes))

    # 7. 失败重试：模拟只落两行，续写只能补剩余额度
    failed = svc.create_batch(
        FORMAL_RECORD_IDS, TOTAL_BUDGET, idem_key="retry-key", simulate_failure=True
    )
    retry_batch_no = failed["batch"]["批次编号"]
    retry_lines = svc.list_lines(retry_batch_no)
    written = sum(1 for line in retry_lines if line["写入状态"] == LINE_WRITTEN)
    pending = sum(1 for line in retry_lines if line["写入状态"] == LINE_PENDING)
    check("模拟失败后仅部分额度落账", written == 2 and pending == 3, f"written={written}, pending={pending}")
    check("未完成批次保持待处理", failed["batch"]["状态"] == BATCH_WAITING)
    try:
        svc.close_batch(retry_batch_no)
        check("有待写入额度时禁止闭环", False)
    except ChannelBudgetError:
        check("有待写入额度时禁止闭环", True)
    cont = svc.continue_batch(retry_batch_no)
    check("续写只补未完成额度", cont["continued"] == 3, str(cont["continued"]))
    retry_lines_after = svc.list_lines(retry_batch_no)
    check("续写后无重复落账", sum(1 for line in retry_lines_after if line["写入状态"] == LINE_WRITTEN) == 5)
    again = svc.continue_batch(retry_batch_no)
    check("再次重试没有可续写额度", again["continued"] == 0)

    # 8. 闭环并回写 LTV 估算列表
    closed = svc.close_batch(retry_batch_no)
    check("批次闭环为单个已闭环数据集", closed["batch"]["状态"] == BATCH_CLOSED)
    life3 = next(row for row in store.rows("lifetime") if row["模型编号"] == "LIFE-0003")
    check("LIFE-0003 回写预算分配动作", life3.get("预算分配动作") == "预算分配（LIFE-0003）", str(life3.get("预算分配动作")))
    check("LIFE-0003 回写关联批次编号", life3.get("预算分配批次") == retry_batch_no)
    life4 = next(row for row in store.rows("lifetime") if row["模型编号"] == "LIFE-0004")
    check("同批次其他引用模型同步回写", life4.get("预算分配动作") == "预算分配（LIFE-0004）")
    close_again = svc.close_batch(retry_batch_no)
    check("重复闭环收敛不报错", close_again["batch"]["状态"] == BATCH_CLOSED)

    # 9. 预算详情口径：同一 LTV 版本、ROI 偏差、消耗速率
    detail_lines = svc.list_lines(retry_batch_no)
    check("详情每行带 ROI 目标偏差", all(isinstance(line["ROI目标偏差"], float) for line in detail_lines))
    check("详情每行带预计消耗速率", all(line["预计消耗速率"] > 0 for line in detail_lines))
    check("额度尾差收口后合计仍等于预算总额",
          round(sum(line["渠道额度"] for line in detail_lines), 2) == TOTAL_BUDGET)

    print("\n==== 回归结果 ====")
    if failures:
        print(f"{len(failures)} 项失败：{failures}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
