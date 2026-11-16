<script lang="ts">
	import type { AdminApiKeyRow } from '$lib/api/admin-api-keys';
	import { focusTrap } from '$lib/actions/focusTrap';

	interface Props {
		keyRow: AdminApiKeyRow;
		onConfirm: (reason: string) => Promise<void> | void;
		onCancel: () => void;
	}
	let { keyRow, onConfirm, onCancel }: Props = $props();

	let reason = $state('');
	let submitting = $state(false);
	let localErr = $state<string | null>(null);

	async function handleSubmit(e: SubmitEvent) {
		e.preventDefault();
		if (reason.trim().length < 10) {
			localErr = 'Motif min. 10 caractères';
			return;
		}
		submitting = true;
		localErr = null;
		try {
			await onConfirm(reason.trim());
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
	aria-labelledby="revoke-key-title"
	tabindex="-1"
	use:focusTrap
>
	<div class="mx-4 w-full max-w-md rounded border border-stone-border bg-parchment p-5 shadow-xl">
		<h2 id="revoke-key-title" class="mb-2 text-xl font-semibold text-ink">
			Révoquer la clé ?
		</h2>
		<p class="mb-4 text-sm text-ink-muted">
			Clé <code class="font-mono">{keyRow.prefix ?? '—'}</code>
			({keyRow.owner_email ?? 'user inconnu'})
			{#if keyRow.is_default}
				<span class="ml-2 rounded bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-800">
					default
				</span>
			{/if}
		</p>
		<form onsubmit={handleSubmit}>
			<label class="block text-sm font-semibold text-ink-muted" for="revoke-reason">
				Motif <span class="text-terracotta">*</span> (min. 10 caractères)
			</label>
			<textarea
				id="revoke-reason"
				bind:value={reason}
				rows="3"
				placeholder="Raison de la révocation…"
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
					class="rounded bg-terracotta px-3 py-1.5 text-sm font-semibold text-white transition-colors hover:opacity-90 disabled:opacity-50"
				>
					{submitting ? 'Révocation…' : 'Révoquer'}
				</button>
			</div>
		</form>
	</div>
</div>
