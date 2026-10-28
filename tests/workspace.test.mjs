import test from 'node:test'
import assert from 'node:assert/strict'
import { getVisibleUtilityTab } from '../src/lib/workspace.ts'

test('automatic summaries keep the tab a mobile reader selected', () => {
  assert.equal(getVisibleUtilityTab(false, 'notes', 'summary'), 'notes')
  assert.equal(getVisibleUtilityTab(false, 'page', 'summary'), 'page')
})

test('desktop can show a requested utility alongside the conversation', () => {
  assert.equal(getVisibleUtilityTab(true, 'notes', 'summary'), 'summary')
  assert.equal(getVisibleUtilityTab(true, 'voice', 'page'), 'page')
})

test('mobile conversation keeps the last utility ready without making it visible', () => {
  assert.equal(getVisibleUtilityTab(false, 'voice', 'notes'), 'notes')
  assert.equal(getVisibleUtilityTab(false, 'summary', 'summary'), 'summary')
})
