import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { RunbookSources } from './Investigation';
import { retrievalInspection } from './testFixture';

afterEach(cleanup);

it('renders saved source excerpts as text independently of evidence', () => {
  render(<RunbookSources state={retrievalInspection().state} />);
  expect(screen.getByRole('heading', { name: 'Runbook guidance' })).toBeTruthy();
  expect(screen.getByText(/<script>window.bad=true<\/script>/)).toBeTruthy();
  expect(document.querySelector('script')).toBeNull();
  expect(screen.getByText('data/runbooks/dns.md')).toBeTruthy();
  expect(screen.queryByText('NXDOMAIN returned before any connection attempt.')).toBeNull();
});

it('distinguishes a saved no-match from a lookup that has not run', () => {
  const { rerender } = render(<RunbookSources state={{ retrieved_sources: [] }} />);
  expect(screen.getByText('No runbook lookup has been saved.')).toBeTruthy();
  rerender(<RunbookSources state={{ retrieved_sources: [], tool_history: [{ name: 'get_runbook_context' }] }} />);
  expect(screen.getByText('The lookup returned no matching passages.')).toBeTruthy();
});

it('shows citation navigation without repeating explanatory copy', () => {
  render(<RunbookSources state={retrievalInspection().state} />);
  expect(screen.getByLabelText('Runbook citations')).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Dependency name resolution · v1' })).toBeTruthy();

});

it('shows cited passage IDs when no matching excerpt was saved', () => {
  render(<RunbookSources state={{ diagnosis: { source_ids: ['missing:runbook#section'] }, retrieved_sources: [],
    tool_history: [{ name: 'get_runbook_context' }] }} />);
  expect(screen.getAllByText('missing:runbook#section').length).toBe(1);
  expect(screen.getAllByText(/passage unavailable/).length).toBe(1);
  expect(screen.getByText('Some cited passages are unavailable.')).toBeTruthy();
});

it('groups passages from the same runbook under one citation without usage labels', () => {
  const state = retrievalInspection().state;
  const sources = state.retrieved_sources as Record<string, unknown>[];
  state.retrieved_sources = [...sources, { ...sources[0], id: 'additional-passage', section: 'Mechanism' }];
  render(<RunbookSources state={state} />);
  expect(screen.getAllByRole('article')).toHaveLength(1);
  expect(screen.getAllByRole('heading', { level: 4 })).toHaveLength(2);
  expect(screen.queryByText('Cited')).toBeNull();
  expect(screen.queryByText('Retrieved')).toBeNull();
  const citations = screen.getByLabelText('Runbook citations');
  expect(citations.querySelectorAll('button')).toHaveLength(1);
  expect(citations.querySelector('button')?.textContent).toBe('Dependency name resolution · v1');
});

it('deduplicates cited passages from one runbook and keeps different versions separate', () => {
  const state = retrievalInspection().state;
  const source = (state.retrieved_sources as Record<string, unknown>[])[0];
  state.retrieved_sources = [source, { ...source, id: 'same-book', section: 'Mechanism' },
    { ...source, id: 'other-version', document_version: '2' }];
  (state.diagnosis as Record<string, unknown>).source_ids = [source.id, 'same-book', 'other-version'];
  render(<RunbookSources state={state} />);
  expect(screen.getAllByRole('article')).toHaveLength(2);
  expect(screen.getByLabelText('Runbook citations').querySelectorAll('button')).toHaveLength(2);
  expect(screen.getByRole('button', { name: 'Dependency name resolution · v1' }).textContent).toBe('Dependency name resolution · v1');
  expect(screen.getByRole('button', { name: 'Dependency name resolution · v2' }).textContent).toBe('Dependency name resolution · v2');
});
