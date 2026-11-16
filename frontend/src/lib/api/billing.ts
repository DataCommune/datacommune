import { api } from './client';

export interface Plan {
	key: string;
	display_name: string;
	quota_monthly: number;
	price_eur: number;
}

export const PLANS: Plan[] = [
	{ key: 'free', display_name: 'Découverte', quota_monthly: 500, price_eur: 0 },
	{ key: 'pro', display_name: 'Pro', quota_monthly: 5000, price_eur: 39 },
	{ key: 'business', display_name: 'Business', quota_monthly: 25000, price_eur: 99 },
	{ key: 'enterprise', display_name: 'Enterprise', quota_monthly: 100000, price_eur: 249 }
];

export function createCheckout(plan: 'pro' | 'business' | 'enterprise') {
	return api<{ url: string }>('/billing/checkout', {
		method: 'POST',
		body: JSON.stringify({ plan })
	});
}

export function getPortalUrl() {
	return api<{ url: string }>('/billing/portal', {
		method: 'POST'
	});
}
