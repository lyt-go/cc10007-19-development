import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import {
  BIZ_CODE,
  CHECK_LIST_CODE,
  NEW_PAGE_CODE,
  fetchBatchDetail,
  type BatchDetail,
} from './api'

function formatMoney(value: number): string {
  return value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export default function ChannelBudgetBatchView() {
  const { batchNo = '' } = useParams()
  const [detail, setDetail] = useState<BatchDetail | null>(null)
  const [errorMessage, setErrorMessage] = useState('')

  const reload = useCallback(async () => {
    try {
      setDetail(await fetchBatchDetail(batchNo))
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '预算详情读取失败')
    }
  }, [batchNo])

  useEffect(() => {
    void reload()
  }, [reload])

  if (errorMessage) {
    return (
      <section className="page" data-check-code={NEW_PAGE_CODE}>
        <p className="error-text">{errorMessage}</p>
        <Link className="link" to="/channel-budget">返回批次清单</Link>
      </section>
    )
  }
  if (!detail) {
    return (
      <section className="page" data-check-code={NEW_PAGE_CODE}>
        <p className="hint">预算详情加载中…</p>
      </section>
    )
  }

  const { batch, lines, checks } = detail
  const quotaSum = lines.reduce((sum, line) => sum + line['渠道额度'], 0)

  return (
    <section className="page" data-module="channel-budget-batch" data-biz-code={BIZ_CODE} data-check-code={NEW_PAGE_CODE}>
      <header className="page-head">
        <div>
          <h2>渠道预算分配明细 · {batch['批次编号']}</h2>
          <p className="page-desc">
            新页面（核验码 {NEW_PAGE_CODE}）读取同一 LTV 版本，展示渠道额度、ROI目标偏差与预计消耗速率；
            批次资料号 {batch['批次资料号']}，业务核验码 {BIZ_CODE}。
          </p>
        </div>
        <div className="page-actions">
          <Link className="btn" to="/channel-budget">
            返回清单核对关联记录（{CHECK_LIST_CODE}）
          </Link>
        </div>
      </header>

      <div className="stat-row">
        <article className="stat-card">
          <span className="stat-label">批次状态</span>
          <strong className="stat-value">{batch['状态']}</strong>
        </article>
        <article className="stat-card">
          <span className="stat-label">预算总额（同一写入）</span>
          <strong className="stat-value">{formatMoney(batch['预算总额'])}</strong>
        </article>
        <article className="stat-card">
          <span className="stat-label">渠道额度合计</span>
          <strong className="stat-value">{formatMoney(quotaSum)}</strong>
        </article>
        <article className="stat-card">
          <span className="stat-label">已写入 / 待写入</span>
          <strong className="stat-value">{batch['已写入额度数']} / {batch['待写入额度数']}</strong>
        </article>
      </div>

      {/* 三项核对：渠道比例、LTV版本、ROI目标偏差 */}
      <div className="panel">
        <div className="panel-head">
          <h3>核对区：渠道比例 · LTV版本 · ROI目标偏差</h3>
        </div>
        <table className="data-table">
          <thead>
            <tr>
              <th>渠道</th>
              <th>渠道比例（同写入）</th>
              <th>LTV版本（同写入）</th>
              <th>ROI目标</th>
              <th>预计ROI</th>
              <th>ROI目标偏差</th>
              <th>偏差率</th>
            </tr>
          </thead>
          <tbody>
            {checks['ROI目标偏差'].map((item) => (
              <tr key={item['渠道']}>
                <td>{item['渠道']}</td>
                <td>{checks['渠道比例核对'][item['渠道']].toFixed(2)}</td>
                <td>{checks['LTV版本核对'][item['渠道']]}</td>
                <td>{item['ROI目标'].toFixed(2)}</td>
                <td>{item['预计ROI'].toFixed(2)}</td>
                <td className={item['ROI目标偏差'] >= 0 ? 'num-ok' : 'num-bad'}>{item['ROI目标偏差'].toFixed(2)}</td>
                <td className={item['ROI目标偏差率'] >= 0 ? 'num-ok' : 'num-bad'}>
                  {(item['ROI目标偏差率'] * 100).toFixed(1)}%
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* 明细数据集 */}
      <div className="panel">
        <div className="panel-head">
          <h3>渠道预算分配明细数据集（承接新能力）</h3>
        </div>
        <table className="data-table">
          <thead>
            <tr>
              <th>行号</th>
              <th>渠道</th>
              <th>渠道额度</th>
              <th>实际分配比例</th>
              <th>价值记录编号</th>
              <th>LTV版本</th>
              <th>观察窗口天</th>
              <th>用户价值合计</th>
              <th>预计消耗速率</th>
              <th>日消耗上限</th>
              <th>写入状态</th>
              <th>落账时间</th>
            </tr>
          </thead>
          <tbody>
            {lines.map((line) => (
              <tr key={line['行号']}>
                <td>{line['行号']}</td>
                <td>{line['渠道']}</td>
                <td>{formatMoney(line['渠道额度'])}</td>
                <td>{(line['实际分配比例'] * 100).toFixed(2)}%</td>
                <td>{line['价值记录编号']}</td>
                <td>{line['LTV版本']}</td>
                <td>{line['观察窗口天']}</td>
                <td>{formatMoney(line['用户价值合计'])}</td>
                <td>
                  {formatMoney(line['预计消耗速率'])}/天
                  {line['速率是否超限'] && <span className="tag tag-bad">超日限</span>}
                </td>
                <td>{formatMoney(line['日消耗上限'])}</td>
                <td>
                  <span className={line['写入状态'] === '已写入' ? 'tag tag-ok' : 'tag tag-bad'}>
                    {line['写入状态']}
                  </span>
                </td>
                <td>{line['落账时间'] ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* 版本裁决与候选依据 */}
      <div className="panel">
        <div className="panel-head">
          <h3>版本裁决：同渠道双版本只引用一个当前价值版本</h3>
        </div>
        <table className="data-table compact">
          <thead>
            <tr>
              <th>渠道</th>
              <th>采用版本</th>
              <th>采用原因</th>
              <th>候选依据（未发布模型只能进入候选分配）</th>
            </tr>
          </thead>
          <tbody>
            {batch['版本裁决'].map((decision) => (
              <tr key={decision['渠道']}>
                <td>{decision['渠道']}</td>
                <td>{decision['采用版本'] ?? '—'}</td>
                <td>{decision['采用原因']}</td>
                <td>
                  {decision['候选依据'].length === 0
                    ? '无'
                    : decision['候选依据'].map((item) => (
                        <span key={item['价值记录编号']} className="tag tag-idle">
                          {item['模型编号']} {item['模型版本']}（{item['发布状态']}）：{item['候选原因']}
                        </span>
                    ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="hint">
          闭环时间：{batch['闭环时间'] ?? '尚未闭环'} ｜ 回写动作：
          {batch['回写动作'] ? batch['回写动作'].join('、') : '待闭环后回写 LTV 估算列表'}
        </p>
      </div>
    </section>
  )
}
