import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it } from 'vitest';
import { DiagnosisSupport } from './Investigation';
import { supportInspection } from './testFixture';

afterEach(cleanup);

it('shows model explanations, declared gaps and full observation context as text', async () => {
  render(<DiagnosisSupport state={supportInspection().state} />);
  expect(screen.getByText('Model explanations for review. Exact excerpts do not confirm causation.')).toBeTruthy();
  expect(screen.getByText('The affected worker and request mapping is unknown.')).toBeTruthy();
  await userEvent.click(screen.getByText('Full observation E1'));
  expect(screen.getByText(/Request mapping is unknown. <script>/)).toBeTruthy();
  expect(document.querySelector('script')).toBeNull();
  expect(document.querySelector('details')?.open).toBe(true);
});

it('distinguishes absent legacy support from a model reporting no gaps', () => {
  const { rerender } = render(<DiagnosisSupport state={{ diagnosis: {} }} />);
  expect(screen.getByText('No support explanation was saved for this result.')).toBeTruthy();
  rerender(<DiagnosisSupport state={{ diagnosis: { support_assessment: { evidence_links: [], blocking_gaps: [] } } }} />);
  expect(screen.getByText('No blocking gaps reported by the model.')).toBeTruthy();
  expect(screen.queryByText('No support explanation was saved for this result.')).toBeNull();
});

it('keeps malformed partial support inspectable without treating it as verified', () => {
  render(<DiagnosisSupport state={{ diagnosis: { support_assessment: { evidence_links: null } } }} />);
  expect(screen.getByText('Saved support format is not recognised.')).toBeTruthy();
  expect(screen.getByText('Support details')).toBeTruthy();
});

it('renders untrusted excerpts and relations without HTML, images or URL links', () => {
  render(<DiagnosisSupport state={{ diagnosis: { support_assessment: { blocking_gaps: [], evidence_links: [
    { evidence_id: 'missing', excerpt: '<img src=x onerror=bad()>', relation: 'https://invalid.example/<script>bad()</script>' },
  ] } } }} />);
  expect(screen.getByText('<img src=x onerror=bad()>')).toBeTruthy();
  expect(document.querySelector('img, script, a')).toBeNull();
  expect(screen.getByText('The cited observation is unavailable.')).toBeTruthy();
});
