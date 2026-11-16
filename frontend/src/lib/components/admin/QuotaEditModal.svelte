<script lang="ts">
	import type { AdminApiKeyRow } from '$lib/api/admin-api-keys';
	import { untrack } from 'svelte';
	import { focusTrap } from '$lib/actions/focusTrap';

	interface Props {
		keyRow: AdminApiKeyRow;
		onConfirm: (quota: number, reason: string) => Promise<void> | void;
		onCancel: () => void;
	}
	let { keyRow, onConfirm, onCancel }: Props = $props();

	function getInitialQuota(row: AdminApiKeyRow): number {
		return row.quota_monthly;
	}

	let quota = $state(getInitialQuota(untrack(() => keyRow)));
	let reason = $state('');
	let submitting = $state(false);
	let localErr = $state<string | null>(null);

	async function handleSubmit(e: SubmitEvent) {
		e.preventDefault();
		if (!Number.isInteger(quota) || quota < 0 || quota > 10_000_000) {
			localErr = 'Quota entre 0 et 10 000 000';
			return;
		}
		if (reason.trim().length < 10) {
			localErr = 'Motif min. 10 caractères';
			return;
		}
		submitting = true;
		localErr = null;
		try {
			await onConfirm(quota, reason.trim());
		} catch (e) {
			localErr = e instanceof Error ? e.message : 'Erreur inattendue';
		} finally {
			submitting = false;
		}
	}
</script>

<!-- Backdrop -->
<div
	class="fixed inset-0 z-50 flex items-center justify-center bg-ink/50"
	role="dialog"
	aria-modal="true"
	aria-labelledby="quota-edit-title"
	tabindex="-1"
	use:focusTrap
>
	<div class="mx-4 w-full max-w-md rounded border border-stone-border bg-parchment p-5 shadow-xl">
		<h2 id="quota-edit-title" class="mb-2 text-xl font-semibold text-ink">
			Modifier le quota
		</h2>
		<p class="mb-4 text-sm text-ink-muted">
			Clé <code class="font-mono">{keyRow.prefix ?? '—'}</code>
			({keyRow.owner_email ?? 'user inconnu'})
			— actuel : {keyRow.quota_monthly.toLocaleString('fr-FR')} req/mois
		</p>
		<form onsubmit={handleSubmit}>
			<label class="block text-sm font-semibold text-ink-muted" for="quota-value">
				Nouveau quota mensuel <span class="text-terracotta">*</span>
			</label>
			<input
				id="quota-value"
				type="number"
				min="0"
				max="10000000"
				bind:value={quota}
				required
				class="mt-1 w-full rounded border border-stone-border bg-white p-2 text-sm text-ink focus:border-bronze focus:outline-none"
			/>
			<label class="mt-3 block text-sm font-semibold text-ink-muted" for="quota-reason">
				Motif <span class="text-terracotta">*</span> (min. 10 caractères)
			</label>
			<textarea
				id="quota-reason"
				bind:value={reason}
				rows="3"
				placeholder="Raison de la modification…"
				class="mt-1 w-full rounded border border-stone-border bg-white p-2 text-sm text-ink focus:border-bronze focus:outline-none"
				required
				minlength="10"
			></textarea>
			{#if localErr}
				<p class="mt-2 text-sm text-terracotta">{localErr}</p>
			{/if}
			<div class="mt-4 flex justify-end gap-2">
				<button
					type="button"
					onclick={onCancel}
					class="rounded border border-stone-border px-3 py-1.5 text-sm text-ink-muted transition-colors hover:bg-marble"
				>
					Annuler
				</button>
				<button
					type="submit"
					disabled={submitting}
					class="rounded bg-bronze px-3 py-1.5 text-sm font-semibold text-white transition-colors hover:bg-bronze-dark disabled:opacity-50"
				>
					{submitting ? 'Enregistrement…' : 'Enregistrer'}
				</button>
			</div>
		</form>
	</div>
</div>
