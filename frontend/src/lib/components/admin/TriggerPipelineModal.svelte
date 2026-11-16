<script lang="ts">
	import { focusTrap } from '$lib/actions/focusTrap';

	interface Props {
		pipelineName: string;
		onConfirm: (reason: string) => Promise<void> | void;
		onCancel: () => void;
	}
	let { pipelineName, onConfirm, onCancel }: Props = $props();

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
		} catch (err) {
			localErr = err instanceof Error ? err.message : 'Erreur inattendue';
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
	aria-labelledby="trigger-pipeline-title"
	tabindex="-1"
	use:focusTrap
>
	<div class="mx-4 w-full max-w-md rounded border border-stone-border bg-parchment p-5 shadow-xl">
		<h2 id="trigger-pipeline-title" class="mb-2 text-xl font-semibold text-ink">
			Déclencher pipeline : {pipelineName} ?
		</h2>
		<p class="mb-4 rounded border border-amber-300 bg-amber-50 p-2 text-xs text-amber-900">
			Un run manuel sera enqueue. Si une exécution est déjà en cours, ce trigger sera silencieusement
			ignoré par le verrou scheduler — aucune exécution parallèle. Action auditée.
		</p>
		<form onsubmit={handleSubmit}>
			<label class="block text-sm font-semibold text-ink-muted" for="trigger-reason">
				Motif <span class="text-terracotta">*</span> (min. 10 caractères)
			</label>
			<textarea
				id="trigger-reason"
				bind:value={reason}
				rows="3"
				placeholder="Raison du trigger manuel…"
				class="mt-1 w-full rounded border border-stone-border bg-white p-2 text-sm text-ink focus:border-bronze focus:outline-none"
				required
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
					{submitting ? 'Envoi…' : 'Déclencher'}
				</button>
			</div>
		</form>
	</div>
</div>
