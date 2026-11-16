<script lang="ts">
	import type { EtlRunRow } from '$lib/api/admin-etl';

	interface Props {
		rows: EtlRunRow[];
	}
	let { rows }: Props = $props();

	function fmt(iso: string | null): string {
		if (!iso) return '—';
		return new Date(iso).toLocaleString('fr-FR');
	}
	function dur(s: number | null): string {
		if (s == null) return '—';
		if (s < 60) return `${s.toFixed(1)}s`;
		if (s < 3600) return `${(s / 60).toFixed(1)}m`;
		return `${(s / 3600).toFixed(2)}h`;
	}
	function statusClass(s: string): string {
		if (s === 'success') return 'bg-emerald-100 text-emerald-800';
		if (s === 'failed') return 'bg-red-100 text-red-800';
		if (s === 'running') return 'bg-amber-100 text-amber-800';
		return 'bg-stone-200 text-ink-muted';
	}
</script>

<table class="w-full border-collapse text-sm">
	<thead class="sticky top-0 bg-marble">
		<tr>
			<th class="border-b border-stone-border px-2 py-1 text-left">ID</th>
			<th class="border-b border-stone-border px-2 py-1 text-left">Début</th>
			<th class="border-b border-stone-border px-2 py-1 text-left">Fin</th>
			<th class="border-b border-stone-border px-2 py-1 text-left">Statut</th>
			<th class="border-b border-stone-border px-2 py-1 text-right">Durée</th>
			<th class="border-b border-stone-border px-2 py-1 text-right">Lignes</th>
			<th class="border-b border-stone-border px-2 py-1 text-left">Erreur</th>
		</tr>
	</thead>
	<tbody>
		{#each rows as r (r.id)}
			<tr class="border-b border-stone-border/50">
				<td class="px-2 py-1 font-mono text-xs">{r.id}</td>
				<td class="px-2 py-1 text-xs">{fmt(r.started_at)}</td>
				<td class="px-2 py-1 text-xs">{fmt(r.completed_at)}</td>
				<td class="px-2 py-1">
					<span class="rounded px-2 py-0.5 text-xs {statusClass(r.status)}">{r.status}</span>
				</td>
				<td class="px-2 py-1 text-right text-xs">{dur(r.total_duration_seconds)}</td>
				<td class="px-2 py-1 text-right text-xs"
					>{r.total_rows_loaded?.toLocaleString('fr-FR') ?? '—'}</td
				>
				<td
					class="max-w-[40ch] truncate px-2 py-1 text-xs text-red-700"
					title={r.error_message ?? ''}
				>
					{r.error_message ?? '—'}
				</td>
			</tr>
		{/each}
	</tbody>
</table>
