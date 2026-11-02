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
