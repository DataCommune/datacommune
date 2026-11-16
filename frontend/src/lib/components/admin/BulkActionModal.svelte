<script lang="ts">
	import { bulkAction, type BulkActionResponse } from '$lib/api/admin-users';
	import { ApiError } from '$lib/api/client';
	import { focusTrap } from '$lib/actions/focusTrap';
	import IconValide from '$lib/components/ds/icons/IconValide.svelte';
	import IconRefuse from '$lib/components/ds/icons/IconRefuse.svelte';

	interface Props {
		action: 'reset_quota' | 'revoke_keys' | 'resend_verification';
		selectedUsers: Array<{ id: number; email: string }>;
		onCancel: () => void;
		onComplete: () => void;
	}

	let { action, selectedUsers, onCancel, onComplete }: Props = $props();

	let reason = $state('');
	let loading = $state(false);
	let error = $state<string | null>(null);
	let results = $state<BulkActionResponse | null>(null);

	const reasonTooShort = $derived(reason.length > 0 && reason.length < 10);
	const canSubmit = $derived(reason.length >= 10 && !loading);

	const actionLabel = $derived(
		({
			reset_quota: 'Reset quota mensuel',
			revoke_keys: 'Révoquer toutes les clés API',
			resend_verification: "Renvoyer l'email de vérification"
		} as Record<string, string>)[action]
	);

	// Build id→email lookup map for result display
	const emailByIdMap = $derived(
		new Map(selectedUsers.map((u) => [u.id, u.email]))
	);

	async function handleConfirm() {
		if (!canSubmit) return;
		error = null;
		loading = true;
		try {
			results = await bulkAction({
				action,
				user_ids: selectedUsers.map((u) => u.id),
				reason
			});
		} catch (e) {
			if (e instanceof ApiError) {
				if (e.status === 422) error = e.detail;
				else if (e.status === 429) error = 'Limite de débit atteinte, réessayez dans un instant.';
				else error = e.detail;
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
		// After submission, prevent accidental backdrop dismissal — require explicit Fermer
		if (results !== null) return;
		if (e.target === e.currentTarget) onCancel();
	}}
	onkeydown={(e) => {
		if (e.key === 'Escape' && results === null) onCancel();
	}}
	role="dialog"
	aria-modal="true"
	tabindex="-1"
	use:focusTrap
>
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="w-full max-w-lg rounded-xl border border-stone-border bg-parchment shadow-xl"
		onclick={(e) => e.stopPropagation()}
	>
		<!-- Header -->
		<div class="flex items-center justify-between border-b border-stone-border px-5 py-4">
			{#if results === null}
				<h2 class="font-semibold text-ink">
					{actionLabel} — {selectedUsers.length} utilisateur{selectedUsers.length > 1 ? 's' : ''}
				</h2>
			{:else}
				<h2 class="font-semibold text-ink">
					Résultats — {results.total - results.failed} réussi{results.total - results.failed > 1 ? 's' : ''},
					{results.failed} échec{results.failed > 1 ? 's' : ''}
				</h2>
			{/if}
			{#if results === null}
				<button
					class="text-xl text-ink-muted transition-colors hover:text-ink"
					onclick={onCancel}
					aria-label="Fermer"
				>&times;</button>
			{/if}
		</div>

		<div class="space-y-4 px-5 py-5">
			{#if results === null}
				<!-- Pre-submit form -->

				<!-- Selected users list -->
				<div>
					<p class="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-muted">
						Utilisateurs ciblés
					</p>
					<div class="max-h-40 overflow-auto rounded border border-stone-border bg-marble-dark px-3 py-2">
						{#each selectedUsers as u}
							<div class="py-0.5 text-sm text-ink">
								<span class="text-ink-muted">#{u.id}</span> — {u.email}
							</div>
						{/each}
					</div>
				</div>

				<!-- Warning for revoke_keys -->
				{#if action === 'revoke_keys'}
					<div class="rounded border border-terracotta bg-terracotta/10 px-3 py-2 text-sm text-terracotta">
						Cette action révoquera toutes les clés API de <strong>{selectedUsers.length}</strong>
						utilisateur{selectedUsers.length > 1 ? 's' : ''}, y compris leurs clés par défaut.
					</div>
				{/if}

				<!-- Reason textarea -->
				<div>
					<label class="mb-1 block text-xs font-semibold uppercase tracking-wide text-ink-muted" for="bulk-reason">
						Raison <span class="text-terracotta">*</span>
					</label>
					<textarea
						id="bulk-reason"
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

				<!-- Error banner -->
				{#if error}
					<div class="rounded border border-terracotta bg-terracotta/10 px-3 py-2 text-sm text-terracotta">
						{error}
					</div>
				{/if}
			{:else}
				<!-- Post-submit results -->
				<div class="max-h-80 overflow-auto space-y-1">
					{#each results.results as r}
						{@const email = emailByIdMap.get(r.user_id) ?? `#${r.user_id}`}
						<!-- D-22 : pictogrammes detournes (coche/croix) remplaces par ds/icons/. La coche
							 portait une classe Tailwind par defaut (emerald) et la croix un token du projet
							 (terracotta) -- le markup etant deja reecrit pour le remplacement d'icone, les
							 deux sont harmonisees sur des tokens (success/terracotta), seule correction de
							 couleur autorisee par le plan 56-11 car indissociable du remplacement. -->
						<div class="flex items-start gap-2 rounded px-2 py-1.5 {r.status === 'ok' ? 'bg-success/10' : 'bg-terracotta/10'}">
							{#if r.status === 'ok'}
								<IconValide class="mt-0.5 h-4 w-4 shrink-0 text-success" />
								<span class="text-sm text-ink">{email}</span>
								<span class="ml-auto text-xs font-semibold text-success">OK</span>
							{:else}
								<IconRefuse class="mt-0.5 h-4 w-4 shrink-0 text-terracotta" />
								<div class="flex-1">
									<span class="text-sm text-ink">{email}</span>
									{#if r.detail}
										<p class="mt-0.5 text-xs text-ink-muted">{r.detail}</p>
									{/if}
								</div>
							{/if}
						</div>
					{/each}
				</div>
			{/if}
		</div>

		<!-- Footer -->
		<div class="flex justify-end gap-2 border-t border-stone-border px-5 py-4">
			{#if results === null}
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
			{:else}
				<button
					class="rounded bg-bronze px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-bronze-dark"
					onclick={onComplete}
				>
					Fermer
				</button>
			{/if}
		</div>
	</div>
</div>
