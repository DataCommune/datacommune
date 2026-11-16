<script lang="ts">
	import type { AdminApiKeyRow } from '$lib/api/admin-api-keys';

	interface Props {
		rows: AdminApiKeyRow[];
		onRevoke: (row: AdminApiKeyRow) => void;
		onEditQuota: (row: AdminApiKeyRow) => void;
	}
	let { rows, onRevoke, onEditQuota }: Props = $props();

	function fmtDate(iso: string | null): string {
		if (!iso) return '—';
		return new Date(iso).toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' });
	}
</script>

<div class="overflow-x-auto">
	<table class="w-full border-collapse text-sm">
		<thead class="sticky top-0 bg-marble">
			<tr>
				<th class="border-b border-stone-border px-2 py-1 text-left text-xs font-semibold text-ink-muted">Préfixe</th>
				<th class="border-b border-stone-border px-2 py-1 text-left text-xs font-semibold text-ink-muted">Owner</th>
				<th class="border-b border-stone-border px-2 py-1 text-right text-xs font-semibold text-ink-muted">Usage mois</th>
				<th class="border-b border-stone-border px-2 py-1 text-left text-xs font-semibold text-ink-muted">État</th>
				<th class="border-b border-stone-border px-2 py-1 text-left text-xs font-semibold text-ink-muted">Dernière util.</th>
				<th class="border-b border-stone-border px-2 py-1 text-left text-xs font-semibold text-ink-muted">Créée</th>
				<th class="border-b border-stone-border px-2 py-1 text-right text-xs font-semibold text-ink-muted">Actions</th>
			</tr>
		</thead>
		<tbody>
			{#each rows as r (r.id)}
				<tr class="border-b border-stone-border/50 hover:bg-marble/50">
					<td class="px-2 py-1.5 font-mono text-xs">{r.prefix ?? '—'}</td>
					<td class="px-2 py-1.5 text-xs">{r.owner_email ?? '—'}</td>
					<td class="px-2 py-1.5 text-right text-xs">
						{r.quota_monthly < 0 ? `Illimité · ${r.quota_used.toLocaleString('fr-FR')} utilisées` : `${r.quota_used.toLocaleString('fr-FR')} / ${r.quota_monthly.toLocaleString('fr-FR')}`}
					</td>
					<td class="px-2 py-1.5 text-xs">
						{#if r.revoked}
							<span class="rounded bg-red-100 px-2 py-0.5 text-xs font-semibold text-red-800">révoquée</span>
						{:else}
							<span class="rounded bg-emerald-100 px-2 py-0.5 text-xs font-semibold text-emerald-800">active</span>
						{/if}
						{#if r.is_default}
							<span class="ml-1 rounded bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-800">default</span>
						{/if}
					</td>
					<td class="px-2 py-1.5 text-xs text-ink-muted">{fmtDate(r.last_used_at)}</td>
					<td class="px-2 py-1.5 text-xs text-ink-muted">{fmtDate(r.created_at)}</td>
					<td class="px-2 py-1.5 text-right">
						<button
							disabled={r.revoked || r.quota_monthly < 0}
							onclick={() => onEditQuota(r)}
							class="mr-1 rounded border border-bronze px-2 py-0.5 text-xs text-bronze transition-colors hover:bg-bronze hover:text-white disabled:cursor-not-allowed disabled:opacity-40"
							title={r.quota_monthly < 0 ? 'Compte interne protégé' : 'Modifier le quota'}
						>
							Quota
						</button>
						<button
							disabled={r.revoked || r.quota_monthly < 0}
							onclick={() => onRevoke(r)}
							class="rounded border border-terracotta px-2 py-0.5 text-xs text-terracotta transition-colors hover:bg-terracotta hover:text-white disabled:cursor-not-allowed disabled:opacity-40"
							title={r.quota_monthly < 0 ? 'Clé interne gérée par le provisioner sécurisé' : 'Révoquer la clé'}
						>
							Révoquer
						</button>
					</td>
				</tr>
			{/each}
			{#if rows.length === 0}
				<tr>
					<td colspan="7" class="px-2 py-6 text-center text-sm text-ink-muted">Aucune clé API</td>
				</tr>
			{/if}
		</tbody>
	</table>
</div>
