<script lang="ts">
	import type { AdminSubscriptionRow } from '$lib/api/admin-subscriptions';

	interface Props {
		rows: AdminSubscriptionRow[];
		onSync: (row: AdminSubscriptionRow) => void;
	}

	let { rows, onSync }: Props = $props();

	function fmtDate(iso: string | null): string {
		if (!iso) return '—';
		return new Date(iso).toLocaleDateString('fr-FR', {
			day: '2-digit',
			month: '2-digit',
			year: 'numeric'
		});
	}

	function sourceLabel(s: string | null): string {
		if (s === 'stripe') return 'stripe';
		if (s === 'manual_grant') return 'grant manuel';
		return 'inconnu';
	}

	function sourceClass(s: string | null): string {
		if (s === 'stripe') return 'bg-bronze/20 text-ink';
		if (s === 'manual_grant') return 'bg-terracotta/20 text-terracotta';
		return 'bg-stone-200 text-ink-muted';
	}

	function statusClass(status: string): string {
		if (status === 'active') return 'text-emerald-700';
		if (status === 'canceled' || status === 'incomplete_expired') return 'text-red-700';
		return 'text-ink-muted';
	}
</script>

<div class="overflow-x-auto">
	<table class="w-full border-collapse text-sm">
		<thead class="sticky top-0 bg-marble">
			<tr>
				<th class="border-b border-stone-border px-2 py-1 text-left text-xs font-semibold text-ink-muted">User</th>
				<th class="border-b border-stone-border px-2 py-1 text-left text-xs font-semibold text-ink-muted">Plan</th>
				<th class="border-b border-stone-border px-2 py-1 text-left text-xs font-semibold text-ink-muted">Statut</th>
				<th class="border-b border-stone-border px-2 py-1 text-left text-xs font-semibold text-ink-muted">Source</th>
				<th class="border-b border-stone-border px-2 py-1 text-left text-xs font-semibold text-ink-muted">Stripe ID</th>
				<th class="border-b border-stone-border px-2 py-1 text-left text-xs font-semibold text-ink-muted">Fin période</th>
				<th class="border-b border-stone-border px-2 py-1 text-right text-xs font-semibold text-ink-muted">Actions</th>
			</tr>
		</thead>
		<tbody>
			{#each rows as r (r.id)}
				<tr class="border-b border-stone-border/50 hover:bg-marble/50">
					<td class="px-2 py-1.5 text-xs">{r.user_email ?? '—'}</td>
					<td class="px-2 py-1.5 text-xs">{r.plan_name}</td>
					<td class="px-2 py-1.5 text-xs {statusClass(r.status)}">{r.status}</td>
					<td class="px-2 py-1.5 text-xs">
						<span class="rounded px-2 py-0.5 text-xs {sourceClass(r.source)}">
							{sourceLabel(r.source)}
						</span>
					</td>
					<td class="px-2 py-1.5 font-mono text-xs text-ink-muted" title={r.stripe_subscription_id}>
						{r.stripe_subscription_id.length > 20
							? r.stripe_subscription_id.slice(0, 17) + '…'
							: r.stripe_subscription_id}
					</td>
					<td class="px-2 py-1.5 text-xs text-ink-muted">{fmtDate(r.current_period_end)}</td>
					<td class="px-2 py-1.5 text-right">
						{#if r.source === 'manual_grant'}
							<button
								disabled
								title="Action indisponible pour les grants manuels"
								class="rounded border border-stone-border px-2 py-0.5 text-xs text-ink-muted opacity-40 cursor-not-allowed"
							>
								Sync Stripe
							</button>
						{:else}
							<button
								onclick={() => onSync(r)}
								class="rounded border border-bronze px-2 py-0.5 text-xs text-bronze transition-colors hover:bg-bronze hover:text-white"
							>
								Sync Stripe
							</button>
						{/if}
					</td>
				</tr>
			{/each}
			{#if rows.length === 0}
				<tr>
					<td colspan="7" class="px-2 py-6 text-center text-sm text-ink-muted">Aucun abonnement</td>
				</tr>
			{/if}
		</tbody>
	</table>
</div>
