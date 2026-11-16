<script lang="ts">
	// The key figures of the commune and of a DPE document as tiles (04.8-04, UI-SPEC v4 § 3.2): a
	// `dl.cles` of four `div.cle` in the validated order, the DOM order being the reading and tab order.
	// Four columns, two under 1080 px, one under 400 px. Without a model (client navigation still
	// loading) it shows four skeleton tiles, busy and hidden from assistive technologies, never a figure.
	import type { KpiCommune } from '$lib/data/commune-kpi';
	import type { KpiDocument } from '$lib/data/document-dpe';
	import Kpi from './Kpi.svelte';

	// `textes`: prose of the page with its acronyms spelled out (04.8-08), read by each tile. `forme`:
	// form of the button of each tile (`SourceButton`).
	let {
		kpis,
		etiquette,
		textes,
		forme
	}: { kpis: (KpiCommune | KpiDocument)[] | null; etiquette?: string; textes?: ReadonlyMap<string, string>; forme?: 'source' | 'detail' } = $props();
</script>

{#if kpis}
	<dl class="cles kpi-strip" aria-label={etiquette}>
		{#each kpis as kpi (kpi.id)}
			<Kpi {kpi} {textes} {forme} />
		{/each}
	</dl>
{:else}
	<div class="cles kpi-strip" aria-hidden="true" data-squelette>
		{#each [1, 2, 3, 4] as rang (rang)}
			<div class="cle-squelette">
				<span class="squelette-ligne squelette-courte"></span>
				<span class="squelette-ligne squelette-valeur"></span>
				<span class="squelette-ligne"></span>
			</div>
		{/each}
	</div>
{/if}

<style>
	.kpi-strip {
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr));
		gap: var(--s2);
		margin: var(--s5) 0;
	}

	/* Two or three tiles fill their panel, as in `Tuile.svelte`. The tiles are rendered by `Kpi.svelte`:
	   a scoped child selector would wait for this component's class on them and never match. */
	@media (min-width: 1081px) {
		.kpi-strip:has(> :global(:nth-child(2):last-child)) {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}

		.kpi-strip:has(> :global(:nth-child(3):last-child)) {
			grid-template-columns: repeat(3, minmax(0, 1fr));
		}
	}

	.cle-squelette {
		display: grid;
		gap: var(--s3);
		min-height: 120px;
		padding: var(--s3) var(--s4);
		border: 1px solid var(--color-line);
		border-radius: var(--radius-tuile);
		background: var(--color-bg);
	}

	.squelette-ligne {
		display: block;
		height: 12px;
		border-radius: var(--radius-badge);
		background: var(--color-line);
	}

	.squelette-courte {
		width: 60%;
	}

	.squelette-valeur {
		width: 80%;
		height: 28px;
	}

	@media (max-width: 1080px) {
		.kpi-strip {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
	}

	@media (max-width: 400px) {
		.kpi-strip {
			grid-template-columns: minmax(0, 1fr);
		}
	}
</style>
