<script lang="ts">
	// The yearly medians of a tile as a small curve (decision of Ianis of 2026-11-11): one point per year
	// that has a served median, joined to the next one when the two years follow each other. A year without
	// a median leaves a hole, nothing is interpolated. The scale is the curve's own (its lowest and highest
	// median), so it shows a direction, never a size: the figures are read in the panel.
	import { descriptionCourbe, type PointTendance } from '$lib/data/commune-tendance';

	let { points }: { points: readonly PointTendance[] } = $props();

	const LARGEUR = 120;
	const HAUTEUR = 32;
	const MARGE = 4;

	const traces = $derived.by(() => {
		const connus = points.filter((point) => point.prix != null).map((point) => point.prix!);
		if (connus.length < 2) return [];
		const [bas, haut] = [Math.min(...connus), Math.max(...connus)];
		const pas = points.length > 1 ? (LARGEUR - 2 * MARGE) / (points.length - 1) : 0;
		return points.map((point, index) => ({
			annee: point.annee,
			x: MARGE + index * pas,
			// A flat series is drawn in the middle of the frame.
			y: point.prix == null ? null : haut === bas ? HAUTEUR / 2 : HAUTEUR - MARGE - ((point.prix - bas) / (haut - bas)) * (HAUTEUR - 2 * MARGE)
		}));
	});
	const segments = $derived(
		traces.flatMap((point, index) => {
			const suivant = traces[index + 1];
			return point.y != null && suivant?.y != null ? [{ x1: point.x, y1: point.y, x2: suivant.x, y2: suivant.y, cle: point.annee }] : [];
		})
	);
</script>

{#if traces.length}
	<svg class="courbe" viewBox="0 0 {LARGEUR} {HAUTEUR}" width={LARGEUR} height={HAUTEUR} role="img" aria-label={descriptionCourbe(points)} data-courbe>
		{#each segments as segment (segment.cle)}
			<line x1={segment.x1} y1={segment.y1} x2={segment.x2} y2={segment.y2} />
		{/each}
		{#each traces as point (point.annee)}
			{#if point.y != null}<circle cx={point.x} cy={point.y} r="2.5" data-annee={point.annee} />{/if}
		{/each}
	</svg>
{/if}

<style>
	.courbe {
		display: block;
		max-width: 100%;
		overflow: visible;
	}

	.courbe line {
		stroke: var(--c-encre, var(--color-accent));
		stroke-width: 1.5;
		stroke-linecap: round;
	}

	.courbe circle {
		fill: var(--c-encre, var(--color-accent));
	}
</style>
