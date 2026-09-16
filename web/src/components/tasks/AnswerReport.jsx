import React, { useState } from 'react'
import Button from '../ui/Button'
import Alert from '../ui/Alert'
import AnswerAccuracy from './AnswerAccuracy'

const labels = { graded: '已判分', pending: '待判分', saved: '已保存', unavailable: '未获取', unsubmitted: '未提交', score_only: '仅有总分' }
const needsReview = (work) => work.status !== 'graded'
  || Boolean(work.previous_grade)
  || work.correct_questions < work.graded_questions
  || (work.score !== undefined && work.full_score !== undefined && work.score < work.full_score)

export default function AnswerReport({ report = {}, stats = {}, active, pending, error, onRefresh }) {
  const [showAll, setShowAll] = useState(false)
  const works = Array.isArray(report.works) ? report.works : []
  const issues = works.filter(needsReview)
  const visible = showAll ? works : issues
  const running = pending || report.status === 'running'
  const date = report.checked_at ? new Date(report.checked_at * 1000).toLocaleString('zh-CN', { hour12: false }) : null
  return (
    <section className="mt-5 rounded-lg border border-separator bg-surface" aria-labelledby="answer-report-title">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-separator px-5 py-4">
        <div>
          <h2 id="answer-report-title" className="font-semibold">答题表现</h2>
          <p className="mt-1 text-pretty text-xs leading-5 text-label-secondary">{report.scope === 'selected_courses' ? '所选课程当前的章节测验判分' : '本次答题的已获取判分'}{date ? ` · ${date} 更新` : ''}</p>
        </div>
        <Button type="button" variant="outline" disabled={active || running} onClick={onRefresh}>{running ? '正在读取判分…' : '刷新判分'}</Button>
      </header>
      <div className="grid gap-5 px-5 py-5 sm:grid-cols-3">
        <AnswerAccuracy stats={stats} />
        <div><p className="text-xs text-label-secondary">已判分题目</p><p className="mt-2 text-2xl font-semibold tabular-nums">{stats.answer_graded_questions ?? '—'}</p><p className="mt-2 text-xs text-label-secondary">{stats.answer_wrong_questions ?? 0} 题错误 · {stats.answer_partial_questions ?? 0} 题部分正确</p></div>
        <div><p className="text-xs text-label-secondary">待判分题目</p><p className="mt-2 text-2xl font-semibold tabular-nums">{stats.answer_pending_questions ?? '—'}</p><p className="mt-2 text-pretty text-xs text-label-secondary">{stats.answer_unsubmitted_questions ? `${stats.answer_unsubmitted_questions} 题未提交 / 仅保存；` : ''}老师批阅后可重新刷新</p></div>
      </div>
      {running ? <p role="status" className="border-t border-separator px-5 py-3 text-sm tabular-nums text-label-secondary">正在读取所选课程的判分：已检查 {report.completed_chapters ?? 0} / {report.total_chapters || '—'} 个章节。可以离开此页，读取会继续。</p> : null}
      {error || report.error ? <Alert className="mx-5 mb-4" variant="danger">{error || report.error}</Alert> : null}
      {report.status === 'partial' ? <Alert className="mx-5 mb-4" variant="warning">部分判分未能读取。当前正确率仅包含已取得的逐题判分，刷新后可补充。{report.errors?.length ? ` ${report.errors.length} 个章节读取失败。` : ''}</Alert> : null}
      {works.length > 0 ? <>
        <div className="flex flex-wrap items-center gap-2 border-t border-separator px-5 py-3">
          <Button variant={showAll ? 'ghost' : 'outline'} type="button" aria-pressed={!showAll} onClick={() => setShowAll(false)}>未满分与待判分（{issues.length}）</Button>
          <Button variant={showAll ? 'outline' : 'ghost'} type="button" aria-pressed={showAll} onClick={() => setShowAll(true)}>全部测验（{works.length}）</Button>
        </div>
        {visible.length > 0 ? <div className="max-h-96 overflow-auto rounded-b-lg" tabIndex={0} role="region" aria-label="测验判分明细">
          <table className="w-full text-left text-sm"><thead className="sticky top-0 bg-canvas text-xs text-label-secondary"><tr><th className="px-5 py-3 font-medium">课程 / 章节</th><th className="whitespace-nowrap px-3 py-3 font-medium">答对 / 已判分</th><th className="whitespace-nowrap px-3 py-3 font-medium">成绩</th><th className="whitespace-nowrap px-5 py-3 font-medium">状态</th></tr></thead>
            <tbody>{visible.map((work, index) => <tr key={`${work.course_id}:${work.job_id ?? index}`} className="border-t border-separator"><td className="px-5 py-3"><p className="text-xs text-label-secondary">{work.course_title}</p><p className="mt-1 font-medium">{work.chapter_title}</p>{work.reason ? <p className="mt-1 text-xs text-label-secondary">{work.reason}</p> : null}{work.previous_grade ? <p className="mt-1 text-xs text-label-secondary">此前记录：{work.previous_grade.score !== undefined ? `${work.previous_grade.score} / ${work.previous_grade.full_score} 分；` : ''}{work.previous_grade.correct_questions ?? 0} / {work.previous_grade.graded_questions ?? 0} 题答对。此前记录未计入当前正确率。</p> : null}</td><td className="whitespace-nowrap px-3 py-3 tabular-nums">{work.graded_questions > 0 ? `${work.correct_questions} / ${work.graded_questions}` : '—'}</td><td className="whitespace-nowrap px-3 py-3 tabular-nums">{work.score !== undefined ? `${work.score} / ${work.full_score}` : '—'}</td><td className="whitespace-nowrap px-5 py-3 text-xs text-label-secondary">{labels[work.status] ?? '未获取'}</td></tr>)}</tbody>
          </table>
        </div> : <p className="px-5 pb-5 text-sm text-label-secondary">已读取的测验均为满分。</p>}
      </> : !running ? <p className="border-t border-separator px-5 py-4 text-pretty text-sm text-label-secondary">{active ? '任务结束后，可刷新所选课程的完整判分。' : report.status === 'completed' ? '所选课程中暂未发现章节测验。' : '点击“刷新判分”，读取所选课程的已有成绩。此操作只读取判分。'}</p> : null}
    </section>
  )
}
