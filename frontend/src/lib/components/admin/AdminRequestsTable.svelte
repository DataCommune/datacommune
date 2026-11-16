<script lang="ts">
	import type { AdminRequestEntry } from '$lib/api/admin-requests';

	interface Props {
		rows: AdminRequestEntry[];
	}
	let { rows }: Props = $props();

	function fmtTs(iso: string): string {
		return new Date(iso).toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'medium' });
	}

	function statusClass(code: number | null): string {
		if (code === null) return 'text-ink-muted';
		if (code < 300) return 'text-emerald-700';
		if (code < 500) return 'text-amber-700';
		return 'text-red-700';
	}
</script>

<div class="overflow-x-auto">
	<table class="w-full border-collapse text-sm">
		<thead class="sticky top-0 bg-marble">
			<tr>
				<th class="border-b border-stone-border px-2 py-1 text-left text-xs font-semibold text-ink-muted">Timestamp</th>
				<th class="border-b border-stone-border px-2 py-1 text-left text-xs font-semibold text-ink-muted">Méthode</th>
				<th class="border-b border-stone-border px-2 py-1 text-left text-xs font-semibold text-ink-muted">Path</th>
				<th class="border-b border-stone-border px-2 py-1 text-right text-xs font-semibold text-ink-muted">Status</th>
				<th class="border-b border-stone-border px-2 py-1 text-right text-xs font-semibold text-ink-muted">Durée (ms)</th>
				<th class="border-b border-stone-border px-2 py-1 text-left text-xs font-semibold text-ink-muted">User</th>
				<th class="border-b border-stone-border px-2 py-1 text-left text-xs font-semibold text-ink-muted">IP</th>
			</tr>
		</thead>
		<tbody>
			{#each rows as r (r.id)}
				<tr class="border-b border-stone-border/50 hover:bg-marble/50">
					<td class="px-2 py-1 font-mono text-xs">{fmtTs(r.ts)}</td>
					<td class="px-2 py-1 font-mono text-xs">{r.method}</td>
					<td class="max-w-xs truncate px-2 py-1 font-mono text-xs" title={r.path}>{r.path}</td>
					<td class="px-2 py-1 text-right font-mono text-xs {statusClass(r.status_code)}">{r.status_code ?? '—'}</td>
					<td class="px-2 py-1 text-right text-xs text-ink-muted">{r.duration_ms !== null ? r.duration_ms.toFixed(0) : '—'}</td>
					<td class="px-2 py-1 text-xs">{r.user_email ?? '—'}</td>
					<td class="px-2 py-1 font-mono text-xs text-ink-muted">{r.client_ip ?? '—'}</td>
				</tr>
			{/each}
			{#if rows.length === 0}
				<tr>
					<td colspan="7" class="px-2 py-6 text-center text-sm text-ink-muted">Aucune requête</td>
				</tr>
			{/if}
		</tbody>
	</table>
</div>
