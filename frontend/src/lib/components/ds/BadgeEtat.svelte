<script lang="ts">
	// State badge of a criterion (UI-SPEC v4 § 3.6, D-11, D-33): it shows the `{ cle, texte }` that
	// statutDe() (`adresse-rapport.ts`) derives from the served state and tone, and owns no table of
	// words. The word carries the state; colour and border only second it. A neutral served state
	// (`null`) renders nothing. A vigilance is filled in a row (address, building, parcel, radius up to
	// 1 km) and outlined in a tile (sector or commune).
	import type { statutDe } from '$lib/data/adresse-rapport';

	interface Props {
		statut: ReturnType<typeof statutDe>;
		zone?: 'ligne' | 'tuile';
	}

	let { statut, zone = 'ligne' }: Props = $props();
</script>

{#if statut}
	<span
		class="badge badge-{statut.cle}"
		class:badge-plein={statut.cle === 'vigilance' && zone === 'ligne'}
		class:badge-contour={statut.cle === 'vigilance' && zone === 'tuile'}>{statut.texte}</span
	>
{/if}

<style>
	.badge {
		display: inline-block;
		padding: var(--s1) var(--s2);
		border: 1px solid transparent;
		border-radius: var(--radius-badge);
		font-family: var(--font-sans);
		font-size: 14px;
		font-weight: 600;
		line-height: 1;
		white-space: nowrap;
	}

	.badge-plein {
		border-color: var(--color-vigilance-bg);
		background: var(--color-vigilance-bg);
		color: var(--color-warn);
	}

	.badge-contour {
		border-color: var(--color-warn);
		background: transparent;
		color: var(--color-warn);
	}

	/* Nothing to flag: the word alone, in ink. Green is what clicks, never a verdict. */
	.badge-rassurant {
		padding-inline: 0;
		background: transparent;
		color: var(--color-ink);
	}

	.badge-non_concerne {
		padding-inline: 0;
		background: transparent;
		color: var(--color-muted);
		font-weight: 400;
	}

	/* Zero, gap, out of coverage and outage: each its word and its form, whatever the colour. */
	.badge-zero {
		border-color: var(--color-line-strong);
		background: transparent;
		color: var(--color-neutre);
	}

	.badge-manque {
		border: 1px dashed var(--color-line-strong);
		background: var(--color-paper);
		color: var(--color-neutre);
	}

	.badge-hors_couverture {
		border-color: var(--color-line-strong);
		background: repeating-linear-gradient(135deg, var(--color-neutre-fond) 0 6px, var(--color-paper) 6px 9px);
		color: var(--color-neutre);
	}

	.badge-panne {
		border-color: currentColor;
		background: var(--color-panne-fond);
		color: var(--color-danger);
	}

	@media print {
		.badge {
			print-color-adjust: exact;
			-webkit-print-color-adjust: exact;
		}
	}
</style>
