<script lang="ts">
	interface Props {
		selectedCount: number;
		onResetQuota: () => void;
		onRevokeKeys: () => void;
		onResendVerif: () => void;
		onClearSelection: () => void;
	}

	let { selectedCount, onResetQuota, onRevokeKeys, onResendVerif, onClearSelection }: Props =
		$props();
</script>

<div class="mb-3 flex flex-wrap items-center gap-2 rounded border border-stone-border bg-marble-dark px-3 py-2">
	<span class="text-sm font-semibold text-ink">
		{selectedCount} utilisateur{selectedCount > 1 ? 's' : ''} sélectionné{selectedCount > 1 ? 's' : ''}
	</span>
	<div class="ml-auto flex gap-2">
		<button
			class="rounded bg-bronze px-3 py-1.5 text-sm font-semibold text-white hover:bg-bronze-dark"
			onclick={onResetQuota}
		>
			Reset quota
		</button>
		<button
			class="rounded bg-terracotta px-3 py-1.5 text-sm font-semibold text-white hover:opacity-90"
			onclick={onRevokeKeys}
		>
			Révoquer clés
		</button>
		<button
			class="rounded border border-bronze px-3 py-1.5 text-sm font-semibold text-bronze hover:bg-bronze hover:text-white"
			onclick={onResendVerif}
		>
			Renvoyer verif
		</button>
		<button
			class="rounded px-3 py-1.5 text-sm text-ink-muted hover:bg-marble"
			onclick={onClearSelection}
		>
			Désélectionner tout
		</button>
	</div>
</div>
