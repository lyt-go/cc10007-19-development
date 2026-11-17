import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import { request } from '@/api/client'
import {
  BIZ_CODE,
  CHECK_LIST_CODE,
  MULTI_SELECT_CODE,
  OPEN_PAGE_CODE,
  closeBatch,
  continueBatch,
  fetchBatches,
  fetchChannelConfigs,
  fetchValueRecords,
  submitBatch,
  validateSelection,
  type BudgetBatch,
  type ChannelConfig,
  type ValidationResult,
  type ValueRecord,
} from './api'

type Row = ValueRecord
type LifetimeRow = Record<string, string | number | null>

const valueColumns = [
  '价值记录编号',
  '渠道',
  '用户分层',
  '模型编号',
  '模型版本',
  '首次接入时间',
  '观察窗口天',
  '单用户LTV',
  '用户价值合计',
  '估算状态',
  '发布状态',
] as const

function formatMoney(value: number): string {
  return value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export default function ChannelBudgetView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [configs, setConfigs] = useState<ChannelConfig[]>([])
  const [batches, setBatches] = useState<BudgetBatch[]>([])
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [selectedIds, setSelectedIds] = useState<number[]>([])
  const [totalBudget, setTotalBudget] = useState('1000000')
  const [validation, setValidation] = useState<ValidationResult | null>(null)
  const [simulateFailure, setSimulateFailure] = useState(false)
  const [message, setMessage] = useState('')
  const [errorMessage, setErrorMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [lifetimeRows, setLifetimeRows] = useState<LifetimeRow[]>([])

  const reload = useCallback(async () => {
    setErrorMessage('')
    try {
      const [recordPage, configPage, batchPage, lifetimePage] = await Promise.all([
        fetchValueRecords(filters),
        fetchChannelConfigs(),
        fetchBatches({ size: '50' }),
        request('/api/lifetime?size=20').then((res) => res.json()),
      ])
      setRows(recordPage.items)
      setTotal(recordPage.total)
      setConfigs(configPage.items)
      setBatches(batchPage.items)
      setLifetimeRows((lifetimePage.items ?? []) as LifetimeRow[])
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '价值驱动预算分配数据读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  const selectableRows = useMemo(() => rows, [rows])
  const allChecked = selectableRows.length > 0 && selectableRows.every((row) => selectedIds.includes(row.id))

  function toggleRow(id: number) {
    setValidation(null)
    setSelectedIds((prev) => (prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]))
  }

  function toggleAll() {
    setValidation(null)
    setSelectedIds(allChecked ? [] : selectableRows.map((row) => row.id))
  }

  async function handleValidate() {
    setMessage('')
    setErrorMessage('')
    const budget = Number(totalBudget)
    if (!Number.isFinite(budget) || budget <= 0) {
      setErrorMessage('请输入有效的预算总额（正数）')
      return
    }
    if (selectedIds.length === 0) {
      setErrorMessage('请先多选至少一条LTV用户价值记录')
      return
    }
    try {
      const result = await validateSelection(selectedIds, budget)
      setValidation(result)
      setMessage(result['核对通过'] ? '逐条核对通过，已生成整组试算，可整组提交' : '核对未通过，请按逐条核对结果调整选择')
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '核对请求失败')
    }
  }

  async function handleSubmit() {
    setBusy(true)
    setMessage('')
    setErrorMessage('')
    try {
      const budget = Number(totalBudget)
      const idemKey = `${selectedIds.slice().sort((a, b) => a - b).join(',')}|${budget.toFixed(2)}|${new Date().toDateString()}`
      const result = await submitBatch(selectedIds, budget, idemKey, simulateFailure)
      if (!result.ok) {
        setErrorMessage(result.message)
        return
      }
      setMessage(result.message)
      setValidation(null)
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '整组提交失败')
    } finally {
      setBusy(false)
    }
  }

  async function handleContinue(batchNo: string) {
    setBusy(true)
    try {
      const result = await continueBatch(batchNo)
      setMessage(result.message)
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '续写失败')
    } finally {
      setBusy(false)
    }
  }

  async function handleClose(batchNo: string) {
    setBusy(true)
    try {
      // 核验码 197291f0：完成闭环操作后开新页面；19f51da7：新页面展示预算详情
      const result = await closeBatch(batchNo)
      setMessage(result.message)
      await reload()
      window.open(`/channel-budget/batches/${batchNo}`, '_blank')
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '闭环失败')
    } finally {
      setBusy(false)
    }
  }

  const checkMap = useMemo(() => {
    const map = new Map<string, ValidationResult['逐条核对'][number]>()
    validation?.['逐条核对'].forEach((item) => map.set(item['价值记录编号'], item))
    return map
  }, [validation])

  const writtenBack = lifetimeRows.filter((row) => Boolean(row['预算分配动作']))

  return (
    <section className="page" data-module="channel-budget" data-biz-code={BIZ_CODE}>
      <header className="page-head">
        <div>
          <h2>价值驱动渠道预算分配</h2>
          <p className="page-desc">
            按首次接入读取 LTV 用户价值记录与预算渠道配置，计算渠道预算分配明细数据集（核验码 {BIZ_CODE}）。
            预算总额、渠道比例与 LTV 版本同一写入，未完成估算的用户价值不进入正式分配。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn" type="button" onClick={() => window.open('/api/channel-budget/export', '_blank')}>
            导出明细数据集
          </button>
        </div>
      </header>

      <div className="stat-row">
        <article className="stat-card">
          <span className="stat-label">用户价值记录</span>
          <strong className="stat-value">{total}</strong>
        </article>
        <article className="stat-card">
          <span className="stat-label">已选记录（多选）</span>
          <strong className="stat-value">{selectedIds.length}</strong>
        </article>
        <article className="stat-card">
          <span className="stat-label">渠道配置数</span>
          <strong className="stat-value">{configs.length}</strong>
        </article>
        <article className="stat-card">
          <span className="stat-label">处理批次</span>
          <strong className="stat-value">{batches.length}</strong>
        </article>
      </div>

      <form
        className="filter-bar"
        onSubmit={(event) => {
          event.preventDefault()
          void reload()
        }}
      >
        <label className="filter-item">
          <span>渠道</span>
          <input
            value={filters.channel ?? ''}
            placeholder="按渠道检索"
            onChange={(event) => setFilters((prev) => ({ ...prev, channel: event.target.value }))}
          />
        </label>
        <label className="filter-item">
          <span>模型编号</span>
          <input
            value={filters.model_no ?? ''}
            placeholder="如 LIFE-0004"
            onChange={(event) => setFilters((prev) => ({ ...prev, model_no: event.target.value }))}
          />
        </label>
        <label className="filter-item">
          <span>估算状态</span>
          <input
            value={filters.estimate_status ?? ''}
            placeholder="已完成 / 估算中"
            onChange={(event) => setFilters((prev) => ({ ...prev, estimate_status: event.target.value }))}
          />
        </label>
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setFilters({})}>重置条件</button>
      </form>

      {/* 多选区：核验码 19ae84df —— 预算分配需多选多条记录，逐条核对后整组提交 */}
      <div className="panel" data-check-code={MULTI_SELECT_CODE}>
        <div className="panel-head">
          <h3>第一步：多选 LTV 用户价值记录（按首次接入读取，核验码 {MULTI_SELECT_CODE}）</h3>
        </div>
        <table className="data-table">
          <thead>
            <tr>
              <th className="check-cell">
                <input type="checkbox" aria-label="全选当前页" checked={allChecked} onChange={toggleAll} />
              </th>
              {valueColumns.map((column) => (
                <th key={column}>{column}</th>
              ))}
              <th>逐条核对结果</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const checked = selectedIds.includes(row.id)
              const check = checkMap.get(row['价值记录编号'])
              return (
                <tr key={row.id} className={checked ? 'row-selected' : undefined}>
                  <td className="check-cell">
                    <input type="checkbox" checked={checked} onChange={() => toggleRow(row.id)} />
                  </td>
                  {valueColumns.map((column) => (
                    <td key={column}>{String(row[column] ?? '—')}</td>
                  ))}
                  <td>
                    {check ? (
                      <span className={check['通过'] ? 'tag tag-ok' : 'tag tag-bad'}>
                        {check['通过'] ? '核对通过' : check['说明']}
                      </span>
                    ) : (
                      <span className="tag tag-idle">待核对</span>
                    )}
                  </td>
                </tr>
              )
            })}
            {rows.length === 0 && (
              <tr>
                <td className="empty-state" colSpan={valueColumns.length + 2}>暂无用户价值记录</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* 预算总额与渠道配置 */}
      <div className="panel">
        <div className="panel-head">
          <h3>第二步：预算总额与渠道配置（渠道比例合计须为 1）</h3>
        </div>
        <div className="form-inline">
          <label className="filter-item">
            <span>预算总额</span>
            <input value={totalBudget} onChange={(event) => setTotalBudget(event.target.value)} inputMode="decimal" />
          </label>
          <label className="filter-item switch-item">
            <span>演练失败续写</span>
            <input type="checkbox" checked={simulateFailure} onChange={(event) => setSimulateFailure(event.target.checked)} />
          </label>
          <button className="btn" type="button" onClick={() => void handleValidate()} disabled={busy}>
            逐条核对并整组试算
          </button>
          <button
            className="btn primary"
            type="button"
            onClick={() => void handleSubmit()}
            disabled={busy || !validation?.['核对通过']}
            title={validation?.['核对通过'] ? '预算总额、渠道比例、LTV版本将在同一写入完成' : '请先通过逐条核对'}
          >
            整组提交预算方案
          </button>
        </div>
        <table className="data-table compact">
          <thead>
            <tr>
              <th>配置编号</th>
              <th>渠道</th>
              <th>渠道比例</th>
              <th>ROI目标</th>
              <th>日消耗上限</th>
              <th>生效区间</th>
            </tr>
          </thead>
          <tbody>
            {configs.map((config) => (
              <tr key={config.id}>
                <td>{config['配置编号']}</td>
                <td>{config['渠道']}</td>
                <td>{config['渠道比例'].toFixed(2)}</td>
                <td>{config['ROI目标'].toFixed(2)}</td>
                <td>{formatMoney(config['日消耗上限'])}</td>
                <td>{config['生效开始']} ~ {config['生效结束']}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* 核对与试算结果 */}
      {validation && (
        <div className="panel">
          <div className="panel-head">
            <h3>
              第三步：核对渠道比例、LTV版本、ROI目标偏差
              <span className={validation['核对通过'] ? 'tag tag-ok' : 'tag tag-bad'}>
                {validation['核对通过'] ? '整组核对通过' : '整组核对未通过'}
              </span>
            </h3>
          </div>
          <p className="hint">
            渠道比例合计：{validation['渠道比例核对']['比例合计']}
            （{validation['渠道比例核对']['通过'] ? '通过' : '未通过'}）
            {validation['缺失渠道'].length > 0 && `；缺失渠道覆盖：${validation['缺失渠道'].join('、')}`}
          </p>
          <table className="data-table compact">
            <thead>
              <tr>
                <th>渠道</th>
                <th>采用LTV版本</th>
                <th>采用原因</th>
                <th>候选依据</th>
              </tr>
            </thead>
            <tbody>
              {validation['版本裁决'].map((decision) => (
                <tr key={decision['渠道']}>
                  <td>{decision['渠道']}</td>
                  <td>{decision['采用版本'] ?? '—'}</td>
                  <td>{decision['采用原因'] || (decision['配置问题'] ?? '—')}</td>
                  <td>
                    {decision['候选依据'].length === 0
                      ? '无'
                      : decision['候选依据'].map((item) => (
                          <span key={item['价值记录编号']} className="tag tag-idle">
                            {item['模型编号']} {item['模型版本']}：{item['候选原因']}
                          </span>
                        ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {validation['试算明细'].length > 0 && (
            <table className="data-table compact">
              <thead>
                <tr>
                  <th>渠道</th>
                  <th>渠道额度</th>
                  <th>配置比例</th>
                  <th>实际分配比例</th>
                  <th>LTV版本</th>
                  <th>ROI目标</th>
                  <th>预计ROI</th>
                  <th>ROI目标偏差</th>
                  <th>预计消耗速率</th>
                </tr>
              </thead>
              <tbody>
                {validation['试算明细'].map((line) => (
                  <tr key={line['渠道']}>
                    <td>{line['渠道']}</td>
                    <td>{formatMoney(line['渠道额度'])}</td>
                    <td>{line['配置渠道比例'].toFixed(2)}</td>
                    <td>{(line['实际分配比例'] * 100).toFixed(2)}%</td>
                    <td>{line['LTV版本']}</td>
                    <td>{line['ROI目标'].toFixed(2)}</td>
                    <td>{line['预计ROI'].toFixed(2)}</td>
                    <td className={line['ROI目标偏差'] >= 0 ? 'num-ok' : 'num-bad'}>
                      {line['ROI目标偏差'].toFixed(2)}（{(line['ROI目标偏差率'] * 100).toFixed(1)}%）
                    </td>
                    <td>
                      {formatMoney(line['预计消耗速率'])}/天
                      {line['速率是否超限'] && <span className="tag tag-bad">超日限</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* 批次清单：核验码 196766a5 —— 转至清单检查关联记录 */}
      <div className="panel" data-check-code={CHECK_LIST_CODE}>
        <div className="panel-head">
          <h3>渠道预算分配批次清单（按处理批次核对，核验码 {CHECK_LIST_CODE}）</h3>
        </div>
        <table className="data-table compact">
          <thead>
            <tr>
              <th>批次编号</th>
              <th>批次资料号</th>
              <th>状态</th>
              <th>预算总额</th>
              <th>已写入/待写入</th>
              <th>提交时间</th>
              <th>闭环时间</th>
              <th>关联操作</th>
            </tr>
          </thead>
          <tbody>
            {batches.map((batch) => (
              <tr key={batch['批次编号']}>
                <td>
                  <Link className="link" to={`/channel-budget/batches/${batch['批次编号']}`}>
                    {batch['批次编号']}
                  </Link>
                </td>
                <td>{batch['批次资料号']}</td>
                <td>
                  <span className={`tag tag-status-${batch['状态']}`}>{batch['状态']}</span>
                </td>
                <td>{formatMoney(batch['预算总额'])}</td>
                <td>{batch['已写入额度数']} / {batch['待写入额度数']}</td>
                <td>{batch['提交时间']}</td>
                <td>{batch['闭环时间'] ?? '—'}</td>
                <td className="row-actions">
                  {batch['待写入额度数'] > 0 && (
                    <button className="link" type="button" disabled={busy} onClick={() => void handleContinue(batch['批次编号'])}>
                      续写未完成额度
                    </button>
                  )}
                  {batch['状态'] !== '已闭环' && batch['待写入额度数'] === 0 && (
                    <button
                      className="link"
                      type="button"
                      data-check-code={OPEN_PAGE_CODE}
                      title={`完成操作便开新页面（核验码 ${OPEN_PAGE_CODE}）`}
                      disabled={busy}
                      onClick={() => void handleClose(batch['批次编号'])}
                    >
                      闭环并开新页面
                    </button>
                  )}
                  {batch['状态'] === '已闭环' && (
                    <button
                      className="link"
                      type="button"
                      onClick={() => window.open(`/channel-budget/batches/${batch['批次编号']}`, '_blank')}
                    >
                      查看预算详情（新页面）
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {batches.length === 0 && (
              <tr>
                <td className="empty-state" colSpan={8}>暂无处理批次，先多选记录并整组提交</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* LTV 估算列表回写核对 */}
      <div className="panel" data-check-code={CHECK_LIST_CODE}>
        <div className="panel-head">
          <h3>LTV 估算列表关联记录核对（预算分配动作回写，LIFE-0003 为回归样例）</h3>
        </div>
        <table className="data-table compact">
          <thead>
            <tr>
              <th>模型编号</th>
              <th>模型名称</th>
              <th>状态</th>
              <th>预算分配动作</th>
              <th>关联批次</th>
              <th>回写时间</th>
            </tr>
          </thead>
          <tbody>
            {lifetimeRows.map((row) => (
              <tr key={String(row.id)} className={row['预算分配动作'] ? 'row-linked' : undefined}>
                <td>{String(row['模型编号'] ?? '—')}</td>
                <td>{String(row['模型名称'] ?? '—')}</td>
                <td>{String(row['status'] ?? '—')}</td>
                <td>{row['预算分配动作'] ? <span className="tag tag-ok">{String(row['预算分配动作'])}</span> : '—'}</td>
                <td>
                  {row['预算分配批次'] ? (
                    <Link className="link" to={`/channel-budget/batches/${String(row['预算分配批次'])}`}>
                      {String(row['预算分配批次'])}
                    </Link>
                  ) : (
                    '—'
                  )}
                </td>
                <td>{row['预算分配回写时间'] ? String(row['预算分配回写时间']) : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {writtenBack.length === 0 && <p className="hint">尚无回写记录，闭环批次后在此核对关联关系。</p>}
      </div>

      <footer className="page-foot">
        <span>
          共 {total} 条用户价值记录，已选 {selectedIds.length} 条
          {message && <span className="info-text">｜{message}</span>}
        </span>
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
