#!/usr/bin/env python3
"""端到端业务流核验：模拟前端「多选 -> 逐条核对 -> 整组提交 -> 续写 -> 闭环 -> 新页面 -> 清单核对」全链路。

仅依赖标准库 urllib，用 -I 运行。
"""
from __future__ import annotations

import json
import urllib.request

BASE = "http://127.0.0.1:8000"
FORMAL_IDS = [1, 2, 3, 4, 5, 7, 8, 9, 10, 11]
failures: list[str] = []


def call(method: str, path: str, body: object | None = None) -> tuple[int, object]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" -> {detail}" if detail else ""))
    if not condition:
        failures.append(name)


# 1. 多选来源：按首次接入读取用户价值记录
status, page = call("GET", "/api/channel-budget/value-records?size=20")
check("读取用户价值记录列表", status == 200 and page["total"] == 12)

# 2. 含未发布/未完成记录的核对必须被拦下
status, bad = call("POST", "/api/channel-budget/validate",
                   {"record_ids": FORMAL_IDS + [6, 12], "total_budget": 1_000_000})
check("未发布与未完成记录核对不通过", status == 200 and bad["核对通过"] is False)
check("核对结果区分未发布/估算未完成原因",
      any("未发布" in c["说明"] for c in bad["逐条核对"])
      and any("估算未完成" in c["说明"] for c in bad["逐条核对"]))

# 3. 正式多选逐条核对 + 整组试算
status, validation = call("POST", "/api/channel-budget/validate",
                          {"record_ids": FORMAL_IDS, "total_budget": 1_000_000})
check("正式记录整组核对通过", validation["核对通过"] is True)
check("核对渠道比例合计为 1", validation["渠道比例核对"]["通过"] is True)
check("核对输出每渠道 LTV 版本与 ROI 目标偏差",
      all(line["LTV版本"] and isinstance(line["ROI目标偏差"], float) for line in validation["试算明细"]))

# 4. 整组提交：预算总额、渠道比例、LTV版本同一写入 + 携带批次资料
status, created = call("POST", "/api/channel-budget/batches",
                       {"record_ids": FORMAL_IDS, "total_budget": 1_000_000, "idem_key": "e2e-main"})
batch = created["entry"]
bno = batch["批次编号"]
check("整组提交成功并携带当前批次资料",
      created["ok"] and batch["批次资料号"] and batch["业务核验码"] == "1924d3b6")
check("同一写入含预算总额/渠道比例/LTV版本",
      batch["预算总额"] == 1_000_000 and len(batch["渠道比例"]) == 5 and len(batch["LTV版本"]) == 5)

# 5. 重复登记收敛
status, repeat = call("POST", "/api/channel-budget/batches",
                      {"record_ids": FORMAL_IDS, "total_budget": 1_000_000, "idem_key": "e2e-main"})
check("重复登记收敛为单个数据集", repeat["ok"] and "收敛" in repeat["message"]
      and repeat["entry"]["批次编号"] == bno)

# 6. 失败演练：只落两行 -> 闭环被拒 -> 续写补完
status, partial = call("POST", "/api/channel-budget/batches",
                       {"record_ids": FORMAL_IDS, "total_budget": 700_000,
                        "idem_key": "e2e-retry", "simulate_failure": True})
pno = partial["entry"]["批次编号"]
check("模拟失败仅部分额度落账", partial["entry"]["已写入额度数"] == 2
      and partial["entry"]["待写入额度数"] == 3)
status, close_reject = call("POST", f"/api/channel-budget/batches/{pno}/close", {})
check("未完成额度禁止闭环", close_reject["ok"] is False and "续写" in close_reject["message"])
status, cont = call("POST", f"/api/channel-budget/batches/{pno}/continue", {})
check("重试只续写未完成额度", cont["ok"] and cont["entry"]["待写入额度数"] == 0
      and cont["entry"]["已写入额度数"] == 5)
status, cont_again = call("POST", f"/api/channel-budget/batches/{pno}/continue", {})
check("无待写入时续写幂等", cont_again["ok"])

# 7. 闭环 -> 新页面预算详情（同一 LTV 版本、渠道额度、ROI偏差、消耗速率）
status, closed = call("POST", f"/api/channel-budget/batches/{pno}/close", {})
check("闭环成功", closed["ok"] and closed["entry"]["状态"] == "已闭环")
status, detail = call("GET", f"/api/channel-budget/batches/{pno}")
lines = detail["lines"]
check("详情读取同一 LTV 版本",
      all(lines[i]["LTV版本"] == detail["batch"]["LTV版本"][lines[i]["渠道"]] for i in range(len(lines))))
check("详情展示渠道额度/ROI目标偏差/预计消耗速率",
      all("渠道额度" in l and "ROI目标偏差" in l and "预计消耗速率" in l for l in lines))
check("额度合计等于预算总额", round(sum(l["渠道额度"] for l in lines), 2) == 700_000)
check("详情核对区含渠道比例/LTV版本/ROI偏差三项",
      set(detail["checks"]) == {"渠道比例核对", "LTV版本核对", "ROI目标偏差"})

# 8. 转清单核对关联记录：批次清单 + LTV 估算列表回写
status, batches = call("GET", "/api/channel-budget/batches?size=20")
check("批次清单可按处理批次核对", batches["total"] >= 2
      and any(b["批次编号"] == pno and b["状态"] == "已闭环" for b in batches["items"]))
status, lifetime = call("GET", "/api/lifetime?size=20")
life3 = next(r for r in lifetime["items"] if r["模型编号"] == "LIFE-0003")
check("预算分配（LIFE-0003）动作回写到 LTV 估算列表",
      life3.get("预算分配动作") == "预算分配（LIFE-0003）"
      and life3.get("预算分配批次") == pno
      and life3.get("预算分配状态") == "已闭环")
life5 = next(r for r in lifetime["items"] if r["模型编号"] == "LIFE-0005")
check("未发布模型 LIFE-0005 无回写（仅候选）", "预算分配动作" not in life5)

# 9. 不存在的批次给出 404
status, missing = call("GET", "/api/channel-budget/batches/CBA-not-exist")
check("未知批次返回 404", status == 404)

print("\n==== E2E 结果 ====")
if failures:
    print(f"{len(failures)} 项失败：{failures}")
    raise SystemExit(1)
print("全部通过")
