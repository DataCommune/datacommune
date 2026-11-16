<script lang="ts">
	import type { AdminEtlPipelineRow } from '$lib/api/admin-etl';

	interface Props {
		rows: AdminEtlPipelineRow[];
		onTrigger: (row: AdminEtlPipelineRow) => void;
		onSelect: (pipelineName: string) => void;
	}
	let { rows, onTrigger, onSelect }: Props = $props();

	function fmtDate(iso: string | null): string {
		if (!iso) return '—';
		return new Date(iso).toLocaleString('fr-FR', {
			day: '2-digit',
			month: '2-digit',
			year: 'numeric',
			hour: '2-digit',
			minute: '2-digit'
		});
	}
	function fmtDuration(s: number | null): string {
		if (s == null) return '—';
		if (s < 60) return `${s.toFixed(1)}s`;
		if (s < 3600) return `${(s / 60).toFixed(1)}m`;
		return `${(s / 3600).toFixed(2)}h`;
	}
	function fmtRows(n: number | null): string {
		if (n == null) return '—';
		return n.toLocaleString('fr-FR');
	}
	function statusClass(s: string | null): string {
		if (s === 'success') return 'bg-emerald-100 text-emerald-800';
		if (s === 'failed') return 'bg-red-100 text-red-800';
		if (s === 'running') return 'bg-amber-100 text-amber-800';
		return 'bg-stone-200 text-ink-muted';
	}
	function statusLabel(s: string | null): string {
		if (s === 'success') return 'succès';
		if (s === 'failed') return 'échec';
		if (s === 'running') return 'en cours';
		return '—';
	}
</script>

<table class="w-full border-collapse text-sm">
	<thead class="sticky top-0 bg-marble">
		<tr>
			<th class="border-b border-stone-border px-2 py-1 text-left">Pipeline</th>
			<th class="border-b border-stone-border px-2 py-1 text-left">Dernier succès</th>
			<th class="border-b border-stone-border px-2 py-1 text-left">Dernier run</th>
			<th class="border-b border-stone-border px-2 py-1 text-left">Statut</th>
			<th class="border-b border-stone-border px-2 py-1 text-right">Durée</th>
			<th class="border-b border-stone-border px-2 py-1 text-right">Lignes</th>
			<th class="border-b border-stone-border px-2 py-1 text-right">Actions</th>
		</tr>
	</thead>
	<tbody>
		{#each rows as r (r.pipeline)}
			<tr class="border-b border-stone-border/50">
				<td class="px-2 py-1">
					<button
						class="text-bronze underline-offset-2 hover:underline"
						onclick={() => onSelect(r.pipeline)}
					>{r.pipeline}</button>
				</td>
				<td class="px-2 py-1 text-xs text-ink-muted">{fmtDate(r.last_success_at)}</td>
				<td class="px-2 py-1 text-xs text-ink-muted">{fmtDate(r.last_run_started_at)}</td>
				<td class="px-2 py-1">
					<span
						class="rounded px-2 py-0.5 text-xs {statusClass(r.last_run_status)}"
						title={r.last_run_error_message ?? ''}
					>
						{statusLabel(r.last_run_status)}
					</span>
				</td>
				<td class="px-2 py-1 text-right text-xs">{fmtDuration(r.last_run_duration_seconds)}</td>
				<td class="px-2 py-1 text-right text-xs">{fmtRows(r.last_run_rows_loaded)}</td>
				<td class="px-2 py-1 text-right">
					<button
						onclick={() => onTrigger(r)}
						class="rounded border border-bronze px-2 py-0.5 text-xs text-bronze hover:bg-bronze hover:text-parchment"
					>
						Trigger refresh
					</button>
				</td>
			</tr>
		{/each}
	</tbody>
</table>
