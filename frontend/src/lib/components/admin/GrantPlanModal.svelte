<script lang="ts">
	import { grantPlan, type GrantPlanRequest } from '$lib/api/admin-users';
	import { ApiError } from '$lib/api/client';
	import { untrack } from 'svelte';
	import { focusTrap } from '$lib/actions/focusTrap';

	interface Props {
		userId: number;
		userEmail: string;
		currentPlan: string;
		subscriptionSource: string | null;
		onCancel: () => void;
		onSuccess: () => void;
	}

	let { userId, userEmail, currentPlan, subscriptionSource, onCancel, onSuccess }: Props =
		$props();

	const PLAN_RANK: Record<string, number> = { free: 0, pro: 1, business: 2, enterprise: 3 };
	const ALLOWED_PLANS: GrantPlanRequest['plan'][] = ['free', 'pro', 'business', 'enterprise'];

	// Pre-select the current plan when it's one we can grant; avoids accidental
	// downgrade to `free` when admin opens the modal on a Pro/Business user.
	function getInitialPlan(planName: string): GrantPlanRequest['plan'] {
		return ALLOWED_PLANS.includes(planName as GrantPlanRequest['plan'])
			? (planName as GrantPlanRequest['plan'])
			: 'free';
	}

	let plan = $state<GrantPlanRequest['plan']>(getInitialPlan(untrack(() => currentPlan)));
	let reason = $state('');
	let endDate = $state('');
	let downgradeAcknowledged = $state(false);
	let loading = $state(false);
	let error = $state<string | null>(null);

	// Detect a plan downgrade relative to the user's current plan.
	const isDowngrade = $derived(
		PLAN_RANK[plan] < (PLAN_RANK[currentPlan] ?? 0)
	);

	// Reset the downgrade acknowledgement whenever plan selection changes back up.
	$effect(() => {
		if (!isDowngrade) downgradeAcknowledged = false;
	});

	// Client-side validation
	const reasonTooShort = $derived(reason.length > 0 && reason.length < 10);
	const canSubmit = $derived(
		reason.length >= 10 && !loading && (!isDowngrade || downgradeAcknowledged)
	);

	// Show warning if Stripe subscription active and not manual_grant
	const showStripeWarning = $derived(
		subscriptionSource !== null && subscriptionSource !== 'manual_grant'
	);

	// `datetime-local` returns a local-time string without timezone (e.g.
	// `2026-05-01T14:30`). `new Date(...)` parses it in the browser's local TZ
	// and `.toISOString()` serialises it as UTC, giving the backend an
	// unambiguous instant to store in TIMESTAMPTZ.
	function toIsoWithTz(local: string): string | null {
		if (!local) return null;
		const parsed = new Date(local);
		if (Number.isNaN(parsed.getTime())) return null;
		return parsed.toISOString();
	}

	async function handleConfirm() {
		if (!canSubmit) return;
		error = null;
		loading = true;
		try {
			await grantPlan(userId, {
				plan,
				reason,
				end_date: toIsoWithTz(endDate),
				force_downgrade: isDowngrade
			});
			onSuccess();
		} catch (e) {
			if (e instanceof ApiError) {
				if (e.status === 429) {
					error = 'Limite de débit atteinte, réessayez dans un instant.';
				} else if (e.status === 422 && e.detail.includes('yourself')) {
					error = 'Action sur soi-même interdite.';
				} else {
					error = e.detail;
				}
			} else {
				error = 'Erreur inattendue.';
			}
		} finally {
			loading = false;
		}
	}
</script>

<!-- Backdrop -->
<!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
<div
	class="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 backdrop-blur-sm"
	onclick={(e) => {
		if (e.target === e.currentTarget) onCancel();
	}}
	onkeydown={(e) => {
		if (e.key === 'Escape') onCancel();
	}}
	role="dialog"
	aria-modal="true"
	tabindex="-1"
	use:focusTrap
>
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="w-full max-w-md rounded-xl border border-stone-border bg-parchment shadow-xl"
		onclick={(e) => e.stopPropagation()}
	>
		<!-- Header -->
		<div class="flex items-center justify-between border-b border-stone-border px-5 py-4">
			<h2 class="font-semibold text-ink">Grant Plan</h2>
			<button
				class="text-xl text-ink-muted transition-colors hover:text-ink"
				onclick={onCancel}
				aria-label="Fermer"
			>&times;</button>
		</div>

		<div class="space-y-4 px-5 py-5">
			<!-- Stripe warning -->
			{#if showStripeWarning}
				<div class="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800">
					L'utilisateur a un abonnement Stripe actif — le grant manuel l'écrasera localement.
				</div>
			{/if}

			<!-- Plan selector -->
			<div>
				<label class="mb-1 block text-xs font-semibold uppercase tracking-wide text-ink-muted" for="grant-plan">
					Plan
				</label>
				<select
					id="grant-plan"
					bind:value={plan}
					class="w-full rounded border border-stone-border bg-white px-3 py-2 text-sm text-ink focus:border-bronze focus:outline-none"
				>
					<option value="free">Free</option>
					<option value="pro">Pro</option>
					<option value="business">Business</option>
					<option value="enterprise">Enterprise</option>
				</select>
			</div>

			<!-- Reason -->
			<div>
				<label class="mb-1 block text-xs font-semibold uppercase tracking-wide text-ink-muted" for="grant-reason">
					Raison <span class="text-terracotta">*</span>
				</label>
				<textarea
					id="grant-reason"
					bind:value={reason}
					placeholder="Minimum 10 caractères…"
					rows="3"
					class="w-full rounded border border-stone-border bg-white px-3 py-2 text-sm text-ink focus:border-bronze focus:outline-none
						{reasonTooShort ? 'border-terracotta' : ''}"
				></textarea>
				{#if reasonTooShort}
					<p class="mt-0.5 text-xs text-terracotta">La raison doit faire au moins 10 caractères.</p>
				{/if}
			</div>

			<!-- End date (optional) -->
			<div>
				<label class="mb-1 block text-xs font-semibold uppercase tracking-wide text-ink-muted" for="grant-end-date">
					Date de fin <span class="text-ink-muted">(optionnel)</span>
				</label>
				<input
					id="grant-end-date"
					type="datetime-local"
					bind:value={endDate}
					class="w-full rounded border border-stone-border bg-white px-3 py-2 text-sm text-ink focus:border-bronze focus:outline-none"
				/>
			</div>

			<!-- Downgrade confirmation -->
			{#if isDowngrade}
				<div class="rounded border border-terracotta bg-terracotta/10 px-3 py-2 text-sm text-terracotta">
					<p class="mb-2 font-semibold">
						Attention : le plan <strong>{plan}</strong> est inférieur au plan actuel
						<strong>{currentPlan}</strong>.
					</p>
					<label class="flex cursor-pointer items-start gap-2">
						<input
							type="checkbox"
							bind:checked={downgradeAcknowledged}
							class="mt-0.5 accent-terracotta"
						/>
						<span>Je confirme vouloir rétrograder cet utilisateur.</span>
					</label>
				</div>
			{/if}

			<!-- Confirmation summary -->
			{#if reason.length >= 10}
				<div class="rounded bg-marble-dark px-3 py-2 text-sm text-ink-muted">
					Grant <strong class="text-ink">{plan}</strong> pour <strong class="text-ink">{userEmail}</strong>
					— raison : {reason}. Confirmer ?
				</div>
			{/if}

			<!-- Error -->
			{#if error}
				<div class="rounded border border-terracotta bg-terracotta/10 px-3 py-2 text-sm text-terracotta">
					{error}
				</div>
			{/if}
		</div>

		<!-- Footer -->
		<div class="flex justify-end gap-2 border-t border-stone-border px-5 py-4">
			<button
				class="rounded px-4 py-2 text-sm text-ink-muted transition-colors hover:bg-marble-dark"
				onclick={onCancel}
				disabled={loading}
			>
				Annuler
			</button>
			<button
				class="rounded bg-bronze px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-bronze-dark disabled:cursor-not-allowed disabled:opacity-50"
				onclick={handleConfirm}
				disabled={!canSubmit}
			>
				{loading ? 'En cours…' : 'Confirmer'}
			</button>
		</div>
	</div>
</div>
