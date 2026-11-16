<script lang="ts">
	import { resetQuota } from '$lib/api/admin-users';
	import { ApiError } from '$lib/api/client';
	import { focusTrap } from '$lib/actions/focusTrap';

	interface Props {
		userId: number;
		userEmail: string;
		quotaUsed: number;
		quotaMonthly: number;
		onCancel: () => void;
		onSuccess: () => void;
	}

	let { userId, userEmail, quotaUsed, quotaMonthly, onCancel, onSuccess }: Props = $props();

	let reason = $state('');
	let loading = $state(false);
	let error = $state<string | null>(null);

	const reasonTooShort = $derived(reason.length > 0 && reason.length < 10);
	const canSubmit = $derived(reason.length >= 10 && !loading);

	async function handleConfirm() {
		if (!canSubmit) return;
		error = null;
		loading = true;
		try {
			await resetQuota(userId, { reason });
			onSuccess();
		} catch (e) {
			if (e instanceof ApiError) {
				if (e.status === 429) {
					error = 'Limite de débit atteinte, réessayez dans un instant.';
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
			<h2 class="font-semibold text-ink">Reset quota mensuel</h2>
			<button
				class="text-xl text-ink-muted transition-colors hover:text-ink"
				onclick={onCancel}
				aria-label="Fermer"
			>&times;</button>
		</div>

		<div class="space-y-4 px-5 py-5">
			<!-- Current usage summary -->
			<div class="rounded bg-marble-dark px-3 py-2 text-sm text-ink-muted">
				{quotaUsed} / {quotaMonthly} requêtes ce mois — passera à 0
			</div>

			<!-- Reason -->
			<div>
				<label class="mb-1 block text-xs font-semibold uppercase tracking-wide text-ink-muted" for="reset-quota-reason">
					Raison <span class="text-terracotta">*</span>
				</label>
				<textarea
					id="reset-quota-reason"
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

			<!-- Confirmation summary -->
			{#if reason.length >= 10}
				<div class="rounded bg-marble-dark px-3 py-2 text-sm text-ink-muted">
					Reset quota pour <strong class="text-ink">{userEmail}</strong>
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
