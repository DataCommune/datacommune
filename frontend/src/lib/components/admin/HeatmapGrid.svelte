<script lang="ts">
	import type { HeatmapDay } from '$lib/api/admin-overview';

	interface Props {
		days: HeatmapDay[];
		retentionDays: number;
	}
	let { days, retentionDays }: Props = $props();

	const today = new Date();

	// Build 30 cells: index 0 = 29 days ago, index 29 = today
	let cells = $derived.by(() => {
		return Array.from({ length: 30 }, (_, i) => {
			const d = new Date(today);
			d.setDate(d.getDate() - (29 - i));
			const iso = d.toISOString().slice(0, 10);
			const hit = days.find((p) => p.day === iso);
			// Within retention window: the last retentionDays cells (from today backward)
			const inRetention = 29 - i < retentionDays;
			return { iso, count: hit?.count ?? 0, inRetention };
		});
	});

	function bucket(count: number, inRetention: boolean): string {
		if (!inRetention) return 'bg-stone-200';
		if (count === 0) return 'bg-marble border border-stone-border';
		if (count <= 10) return 'bg-bronze/20';
		if (count <= 100) return 'bg-bronze/40';
		if (count <= 500) return 'bg-bronze/60';
		return 'bg-bronze';
	}
</script>

<div
	class="grid grid-cols-7 gap-1"
	role="grid"
	aria-label="Heatmap requêtes — 30 derniers jours"
>
	{#each cells as cell}
		<div
			role="gridcell"
			class="h-5 w-5 rounded {bucket(cell.count, cell.inRetention)}"
			title={cell.inRetention
				? `${cell.iso} : ${cell.count.toLocaleString('fr-FR')} requête${cell.count !== 1 ? 's' : ''}`
				: `${cell.iso} : hors rétention`}
		></div>
	{/each}
</div>
<p class="mt-2 text-xs text-ink-muted">
	Données disponibles sur {retentionDays} jour{retentionDays !== 1 ? 's' : ''} (rétention
	<code class="font-mono">api_request_log</code>). Les cellules grises = plus d'activité disponible.
</p>
