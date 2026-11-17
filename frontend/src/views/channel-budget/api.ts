/** 价值驱动渠道预算分配：接口类型与请求封装。 */
import { request } from '@/api/client'

export const BIZ_CODE = '1924d3b6'
/** 多选多条记录核验码：预算分配需多选后逐条核对、整组提交 */
export const MULTI_SELECT_CODE = '19ae84df'
/** 完成操作便开新页面核验码 */
export const OPEN_PAGE_CODE = '197291f0'
/** 新页面核验码：预算详情页 */
export const NEW_PAGE_CODE = '19f51da7'
/** 清单核对关联记录核验码：批次清单与 LTV 列表回写 */
export const CHECK_LIST_CODE = '196766a5'

const ENDPOINT = '/api/channel-budget'

export type ValueRecord = {
  id: number
  价值记录编号: string
  模型编号: string
  模型版本: string
  渠道: string
  用户分层: string
  首次接入时间: string
  观察窗口天: number
  预测窗口天: number
  单用户LTV: number
  覆盖新增用户数: number
  用户价值合计: number
  估算状态: string
  发布状态: string
  status: string
}

export type ChannelConfig = {
  id: number
  配置编号: string
  渠道: string
  渠道比例: number
  ROI目标: number
  日消耗上限: number
  生效开始: string
  生效结束: string
}

export type PreviewLine = {
  渠道: string
  渠道额度: number
  配置渠道比例: number
  实际分配比例: number
  LTV模型编号: string
  LTV版本: string
  价值记录编号: string
  观察窗口天: number
  预测窗口天: number
  用户价值合计: number
  ROI目标: number
  预计ROI: number
  ROI目标偏差: number
  ROI目标偏差率: number
  预计消耗速率: number
  日消耗上限: number
  速率是否超限: boolean
  写入状态: string
}

export type RecordCheck = {
  价值记录编号: string
  渠道?: string
  模型编号?: string
  模型版本?: string
  估算状态?: string
  发布状态?: string
  通过: boolean
  说明: string
}

export type VersionDecision = {
  渠道: string
  采用版本: string | null
  采用原因: string
  候选依据: {
    价值记录编号: string
    模型编号: string
    模型版本: string
    观察窗口天: number
    发布状态: string
    估算状态: string
    候选原因: string
  }[]
  配置问题: string | null
}

export type ValidationResult = {
  业务核验码: string
  核对通过: boolean
  逐条核对: RecordCheck[]
  渠道比例核对: { 比例合计: number; 通过: boolean }
  缺失渠道: string[]
  版本裁决: VersionDecision[]
  试算明细: PreviewLine[]
}

export type BudgetBatch = {
  id: number
  批次编号: string
  批次资料号: string
  业务核验码: string
  状态: string
  预算总额: number
  渠道比例: Record<string, number>
  LTV版本: Record<string, string>
  渠道额度: Record<string, number>
  版本裁决: VersionDecision[]
  幂等键: string
  操作人: string
  提交时间: string
  待写入额度数: number
  已写入额度数: number
  闭环时间: string | null
  回写动作: string[] | null
}

export type BatchLine = PreviewLine & { 行号: number; 落账时间?: string }

export type BatchDetail = {
  biz_code: string
  batch: BudgetBatch
  lines: BatchLine[]
  checks: {
    渠道比例核对: Record<string, number>
    LTV版本核对: Record<string, string>
    ROI目标偏差: {
      渠道: string
      ROI目标: number
      预计ROI: number
      ROI目标偏差: number
      ROI目标偏差率: number
    }[]
  }
}

async function unwrap<T>(response: Response): Promise<T> {
  const payload = await response.json()
  if (!response.ok) {
    throw new Error(payload?.detail ? String(payload.detail) : '接口请求失败')
  }
  return payload as T
}

export async function fetchValueRecords(filters: Record<string, string>): Promise<{ items: ValueRecord[]; total: number }> {
  const query = new URLSearchParams(filters).toString()
  return unwrap(await request(`${ENDPOINT}/value-records?${query}`))
}

export async function fetchChannelConfigs(): Promise<{ items: ChannelConfig[] }> {
  return unwrap(await request(`${ENDPOINT}/channel-configs`))
}

export async function validateSelection(recordIds: number[], totalBudget: number): Promise<ValidationResult> {
  return unwrap(
    await request(`${ENDPOINT}/validate`, {
      method: 'POST',
      body: JSON.stringify({ record_ids: recordIds, total_budget: totalBudget }),
    }),
  )
}

export async function submitBatch(
  recordIds: number[],
  totalBudget: number,
  idemKey: string,
  simulateFailure = false,
): Promise<{ ok: boolean; message: string; entry: BudgetBatch | null }> {
  return unwrap(
    await request(`${ENDPOINT}/batches`, {
      method: 'POST',
      body: JSON.stringify({
        record_ids: recordIds,
        total_budget: totalBudget,
        idem_key: idemKey,
        simulate_failure: simulateFailure,
      }),
    }),
  )
}

export async function continueBatch(batchNo: string): Promise<{ ok: boolean; message: string; entry: BudgetBatch | null }> {
  return unwrap(
    await request(`${ENDPOINT}/batches/${batchNo}/continue`, {
      method: 'POST',
      body: JSON.stringify({}),
    }),
  )
}

export async function closeBatch(batchNo: string): Promise<{ ok: boolean; message: string; entry: BudgetBatch | null }> {
  return unwrap(
    await request(`${ENDPOINT}/batches/${batchNo}/close`, {
      method: 'POST',
      body: JSON.stringify({}),
    }),
  )
}

export async function fetchBatches(filters: Record<string, string>): Promise<{ items: BudgetBatch[]; total: number }> {
  const query = new URLSearchParams(filters).toString()
  return unwrap(await request(`${ENDPOINT}/batches?${query}`))
}

export async function fetchBatchDetail(batchNo: string): Promise<BatchDetail> {
  return unwrap(await request(`${ENDPOINT}/batches/${batchNo}`))
}
