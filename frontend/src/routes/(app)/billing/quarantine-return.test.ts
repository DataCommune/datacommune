import { render } from '@testing-library/svelte';
import { afterEach, expect, it, vi } from 'vitest';
import { writable } from 'svelte/store';
import Billing from './+page.svelte';

const fixture = vi.hoisted(() => ({
  getMe: vi.fn().mockRejectedValue(new Error('verification pending')),
  auth: { user: { plan: 'free' }, setUser: vi.fn() }
}));
vi.mock('$app/stores', () => ({ page: writable({ url: new URL('https://fixture/billing?session_id=untrusted-query') }) }));
vi.mock('$lib/api/auth', () => ({ getMe: fixture.getMe }));
vi.mock('$lib/stores/auth.svelte', () => ({ auth: fixture.auth }));
vi.mock('$lib/api/billing', () => ({ PLANS: [], createCheckout: vi.fn(), getPortalUrl: vi.fn() }));
vi.mock('$lib/api/client', () => ({ ApiError: class extends Error {} }));

afterEach(() => vi.useRealTimers());
it('never treats a query string or a failed refresh as payment confirmation', async () => {
  vi.useFakeTimers();
  const { container } = render(Billing);
  expect(container.textContent).toContain('Retour de Checkout reçu');
  expect(container.textContent).toContain('après vérification');
  expect(container.textContent).not.toContain('Paiement effectué avec succès');
  expect(container.querySelector('.msg-success')).toBeNull();
  await vi.advanceTimersByTimeAsync(2000);
  expect(fixture.getMe).toHaveBeenCalledOnce();
  expect(fixture.auth.setUser).not.toHaveBeenCalled();
  expect(fixture.auth.user.plan).toBe('free');
  expect(container.textContent).toContain('appels admis');
});
