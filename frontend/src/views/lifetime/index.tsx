import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/lifetime'
const columns = [
  "模型编号",
  "模型名称",
  "估算方法",
  "观察窗口天",
  "预测窗口天",
  "折现率",
  "客单价因子",
  "留存率因子",
  "预算分配动作",
  "预算分配批次"
]
const actions = [
  "提交模型校验",
  "启用模型",
  "废弃模型"
]
const statuses = [
  "草稿",
  "模型校验中",
  "已启用",
  "已废弃"
]
const stats = [
  {
    "label": "LTV模型总数",
    "value": 0
  },
  {
    "label": "已启用模型",
    "value": 0
  },
  {
    "label": "校验中模型",
    "value": 0
  }
]

type Row = Record<string, string | number | null>

export default function LifetimeView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('LTV模型列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'LTV估算列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      if (!response.ok) {
        throw new Error('LTV估算动作未生效，请稍后重试')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'LTV估算操作失败')
    }
  }

  return (
    <section className="page" data-module="lifetime">
      <header className="page-head">
        <div>
          <h2>LTV估算管理</h2>
          <p className="page-desc">管理用户生命周期价值的估算模型，配置观察窗口和预测参数。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('LTV模型登记入口尚未接入审批流')}>
            登记LTV模型
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出LTV估算清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        {stats.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {filterFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={filters[field] ?? ''}
              placeholder={`按${field}检索`}
              onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
            />
          </label>
        ))}
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setFilters({})}>重置条件</button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)} className={row['预算分配动作'] ? 'row-linked' : undefined}>
              {columns.map((column) => {
                if (column === '预算分配动作' && row[column]) {
                  return (
                    <td key={column}>
                      <span className="tag tag-ok">{String(row[column])}</span>
                    </td>
                  )
                }
                return <td key={column}>{row[column] ?? '—'}</td>
              })}
              <td className="row-actions">
                {actions.map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>暂无LTV估算数据，可先登记LTV模型</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条LTV估算记录</span>
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
