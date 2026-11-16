<script lang="ts">
	// The one rendering of a served value or of its declared absence (UI-SPEC « Les quatre états
	// servis », v4 § 2.2 and § 3.6). Zero is a value like any other, set in Newsreader 500 with tabular
	// figures; a missing value is the dashed badge of a gap carrying the served sub-state, with its
	// sentence as help; out of coverage is the dotted badge carrying the label of its reason (ochre
	// belongs to the Vigilance, never to an absence). The technical key is never displayed: labels and sentences come from
	// the state glossary (LIBELLES_ETATS, PHRASES_ETATS, LIBELLES_MOTIFS) only.
	import type { EtatKpi } from '$lib/data/commune-kpi';
	import { libelleEtat, phraseEtat } from '$lib/data/etats-libelles';

	interface Props {
		etat: Exclude<EtatKpi, { kind: 'absence-non-declaree' }>;
		/** `kpi`: key figure, 30 px value (24 px up to 720 px); `cellule`: body size. */
		taille?: 'kpi' | 'cellule';
		/** Shows the glossary sentence next to the badge. */
		aide?: boolean;
		/**
		 * A decided label replacing the glossary one for this absence (④ in « Effectif insuffisant »
		 * for both prices, 04-DECISIONS-TEXTES row 39); the state itself stays the served one.
		 */
		libelle?: string;
	}

	let { etat, taille = 'cellule', aide = true, libelle: libelleDecide }: Props = $props();

	// A state the glossary does not know has no honest wording: it is refused (not rendered) and
	// named on the console, like an undeclared absence (commune-kpi.ts) — never « Non renseigné ».
	const libelle = $derived(etat.kind === 'absence' ? (libelleDecide ?? etat.libelle ?? libelleEtat(etat.etat, etat.motif)) : null);
	const refuse = $derived(etat.kind === 'absence' && !libelle);
	// A provisional label (a served reason without wording) has no known cause: no sentence.
	const phrase = $derived(
		etat.kind === 'absence' && aide && !etat.provisoire
			? (etat.aide ?? phraseEtat(etat.etat, etat.motif, etat.effectif))
			: null
	);

	$effect.pre(() => {
		if (refuse && etat.kind === 'absence') {
			console.error(`[StateValue] état sans libellé du glossaire, non affiché : ${etat.etat}`);
		}
	});
</script>

{#if etat.kind === 'valeur'}
	<p class="state-value" class:state-value-kpi={taille === 'kpi'} data-state="value">
		<span class="value">{etat.texte}</span>{#if etat.unite}<span class="unit">{' '}{etat.unite}</span>{/if}
	</p>
{:else if !refuse}
	<p class="state-absence" data-state={etat.etat}>
		<span
			class="state-badge"
			class:state-badge-warn={etat.etat === 'out_of_coverage'}
			data-provisoire={etat.provisoire ? 'true' : undefined}>{libelle}</span
		>
		{#if phrase}<span class="state-help">{phrase}</span>{/if}
	</p>
{/if}

<style>
	.state-value {
		margin: 0;
		color: var(--color-ink);
		font-size: 16px;
		line-height: 1.5;
		font-variant-numeric: tabular-nums;
		overflow-wrap: anywhere;
	}

	.value {
		font-family: var(--font-display);
		font-weight: 500;
	}

	.state-value-kpi .value {
		font-size: 30px;
		line-height: 1.1;
		letter-spacing: -0.01em;
	}

	.unit {
		color: var(--color-muted);
		font-family: var(--font-sans);
		font-size: 14px;
		font-weight: 400;
	}

	.state-value-kpi .unit {
		font-size: 16px;
	}

	.state-absence {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: var(--s1);
		margin: 0;
		font-size: 14px;
		line-height: 1.4;
	}

	/* The badge of a gap (§ 3.6 « Manquant »); a long served label may wrap, hence its line height. */
	.state-badge {
		display: inline-block;
		font-family: var(--font-sans);
		max-width: 100%;
		box-sizing: border-box;
		padding: var(--s1) var(--s2);
		border: 1px dashed var(--color-line-strong);
		border-radius: var(--radius-badge);
		background: var(--color-paper);
		color: var(--color-muted);
		font-weight: 600;
		line-height: 1.3;
		overflow-wrap: anywhere;
	}

	/* Out of coverage (§ 3.6 « Hors couverture »). */
	.state-badge-warn {
		border: 1px dotted var(--color-muted);
		background: transparent;
		color: var(--color-muted);
	}

	.state-help {
		max-width: min(100%, var(--mesure));
		color: var(--color-muted);
		overflow-wrap: anywhere;
	}

	@media (max-width: 720px) {
		.state-value-kpi .value {
			font-size: 24px;
		}
	}
</style>
