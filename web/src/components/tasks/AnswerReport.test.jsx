import React from 'react'
import { render, screen } from '@testing-library/react'
import AnswerReport from './AnswerReport'
import AnswerAccuracy from './AnswerAccuracy'

test('grade regression shows previous evidence alongside the actual current result', () => {
  render(<AnswerReport report={{ status: 'completed', works: [{
    course_id: 'c', job_id: 'w', course_title: '课程', chapter_title: '章节',
    status: 'graded', score: 60, full_score: 100, correct_questions: 3, graded_questions: 5,
    previous_grade: { score: 100, full_score: 100, correct_questions: 5, graded_questions: 5 },
  }] }} stats={{ answer_correct_questions: 3, answer_graded_questions: 5, answer_changed_works: 1 }} />)
  expect(screen.getByLabelText('答题正确率')).toHaveTextContent('60%')
  expect(screen.getByText(/1 份测验的成绩发生回退/)).toBeInTheDocument()
  expect(screen.getByText(/此前记录：100 \/ 100 分；5 \/ 5 题答对/)).toBeInTheDocument()
  expect(screen.getByRole('cell', { name: '60 / 100' })).toBeInTheDocument()
})

test('compact accuracy explains excluded unsubmitted and unavailable results', () => {
  render(<AnswerAccuracy compact stats={{ answer_correct_questions: 8, answer_graded_questions: 10,
    answer_unsubmitted_questions: 90, answer_unavailable_works: 2 }} />)
  expect(screen.getByLabelText('答题正确率')).toHaveTextContent('80%')
  expect(screen.getByText('90 题未提交，未计入正确率')).toBeInTheDocument()
  expect(screen.getByText('2 份测验判分不完整')).toBeInTheDocument()
})
