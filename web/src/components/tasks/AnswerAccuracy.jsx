import React from 'react'

export function answerAccuracy(stats = {}) {
  const graded = Number(stats.answer_graded_questions)
  const correct = Number(stats.answer_correct_questions)
  if (!Number.isFinite(graded) || !Number.isFinite(correct) || graded <= 0 || correct < 0 || correct > graded) return null
  return { graded, correct, percent: Math.round(correct / graded * 1000) / 10 }
}

export default function AnswerAccuracy({ stats = {}, compact = false }) {
  const result = answerAccuracy(stats)
  return (
    <div>
      <p className="text-xs text-label-secondary">答题正确率</p>
      <p aria-label="答题正确率" className="mt-2 text-2xl font-semibold tabular-nums text-label-primary">{result ? `${result.percent}%` : '—'}</p>
      <p className="mt-2 text-xs tabular-nums text-label-secondary">{result ? `${result.correct} / ${result.graded} 题答对 · ${stats.answer_scope_current_courses ? '所选课程' : '本次答题'}` : '暂无平台判分数据'}</p>
      {stats.answer_unsubmitted_questions > 0 ? <p className="mt-2 text-xs text-label-secondary">{stats.answer_unsubmitted_questions} 题未提交，未计入正确率</p> : null}
      {stats.answer_unavailable_works > 0 ? <p className="mt-2 text-xs text-label-secondary">{stats.answer_unavailable_works} 份测验判分不完整</p> : null}
      {stats.answer_changed_works > 0 ? <p className="mt-2 text-xs text-label-secondary">{stats.answer_changed_works} 份测验的成绩发生回退，请查看明细</p> : null}
      {!compact ? <p className="mt-2 text-pretty text-xs leading-5 text-label-secondary">正确率按平台逐题判定计算；待判分题目不计入分母。</p> : null}
    </div>
  )
}
