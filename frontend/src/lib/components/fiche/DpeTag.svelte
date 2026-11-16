<script lang="ts">
	// A DPE energy class as served (A to G): the scale of the contract as background, the letter in
	// ink, or in white at its two dark ends (AA). The colour tells the class apart, it passes no judgement: no other
	// colour of the fiche says « good » or « bad » (UI-SPEC « Couleur »). v4 pastille (UI-SPEC v4
	// § 11, D-30): the badge form of § 3.6, 14 px, and 16 px for the main class of a key figure.
	import type { EnergyClass } from '$lib/api/commune';

	let { classe, principal = false }: { classe: EnergyClass; principal?: boolean } = $props();
</script>

<span class="dpe-tag dpe-tag-{classe}" class:dpe-tag-principal={principal}
	><span class="sr-only">{'Classe '}</span>{classe}</span
>

<style>
	.dpe-tag {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		box-sizing: border-box;
		min-width: 24px;
		padding: var(--s1) var(--s2);
		border-radius: var(--radius-badge);
		color: var(--color-dpe-ink);
		font-size: 14px;
		font-weight: 600;
		line-height: 1;
	}

	.dpe-tag-principal {
		min-width: 32px;
		font-size: 16px;
	}

	.dpe-tag-A {
		background: var(--color-dpe-a);
		color: var(--color-dpe-sur-a);
	}

	.dpe-tag-B {
		background: var(--color-dpe-b);
	}

	.dpe-tag-C {
		background: var(--color-dpe-c);
	}

	.dpe-tag-D {
		background: var(--color-dpe-d);
	}

	.dpe-tag-E {
		background: var(--color-dpe-e);
	}

	.dpe-tag-F {
		background: var(--color-dpe-f);
	}

	.dpe-tag-G {
		background: var(--color-dpe-g);
		color: var(--color-dpe-sur-g);
	}

	@media print {
		.dpe-tag {
			print-color-adjust: exact;
			-webkit-print-color-adjust: exact;
		}
	}
</style>
