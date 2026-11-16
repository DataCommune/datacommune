<script lang="ts">
	// The figures of the head of the address fiche (4.9, reading of 06/11; CADRAGE R1, R8, R9; D-33): one band
	// cut by hairlines, price per m², rent and DPE, each with its scale word. The component shows what the API
	// serves: the served figures, the served state and the served attachment of the source theme. A value that
	// is known takes a tile; a served zero takes one too, in words, with where to go next; a gap, an out of
	// coverage or an outage takes no tile: its word and sentence stand under the tiles, one line per state,
	// never an empty box nor a suggested zero. The build year leaves the head (R9): its value stays on its row.
	import type { Snippet } from 'svelte';
	import type { ChiffreCleRapport, ThemeRapport } from '$lib/api/rapport';
	import BadgeEtat from '$lib/components/ds/BadgeEtat.svelte';
	import IconeCategorie from '$lib/components/ds/IconeCategorie.svelte';
	import RepereEchelle from '$lib/components/ds/RepereEchelle.svelte';
	import {
		LIBELLE_STATUT,
		aplatirDetails,
		libelleCode,
		statutDe,
		valeurChiffre,
		type CleStatut,
		type Statut
	} from '$lib/data/adresse-rapport';
	import { niveauDe } from '$lib/data/echelles';
	import { partieDeTheme } from '$lib/data/questions';
	import { PAS_ZERO } from '$lib/data/recherche-lieux';
	import { typographieFr } from '$lib/utils/typographie';

	interface Props {
		chiffres: readonly ChiffreCleRapport[];
		/** The themes of the report: the source theme of a figure gives its attachment and its sentence. */
		themes: readonly ThemeRapport[];
		/** Prose of the page with its acronyms spelled out (04.8-08, D-31): the label of a figure, `cle:<id>`. */
		textes?: ReadonlyMap<string, string>;
		/** Where a served zero of the DPE sends the reader: the fiche of the commune. */
		lienCommune?: string;
		/** The next step after the price (CADRAGE R10): under its tile, under the band when it has no tile. */
		suitePrix?: Snippet;
	}
	let { chiffres, themes, textes, lienCommune, suitePrix }: Props = $props();

	const NBSP = String.fromCharCode(0xa0); // no-break space
	const HORS_TETE: ReadonlySet<string> = new Set(['annee_construction']);
	const TUILE_PRIX = 'prix_m2_median';
	// The two figures of a tile keep one order, so that the columns of price and rent read across.
	const ORDRE_TYPES = ['appartement', 'maison'];
	const QUI: Record<string, string> = { appartement: 'Appartement', maison: 'Maison' };

	/**
	 * The word of a state without figure: the one the source theme's own row wears (`statutDe`) when it is
	 * in the same state, else the generic word of that state. `ok` has no word: an `ok` without a figure is
	 * what the API itself serves as a gap.
	 */
	function statutSansChiffre(k: ChiffreCleRapport, theme: ThemeRapport | undefined): Statut {
		const statut = theme && theme.etat === k.etat ? statutDe(theme) : null;
		if (statut && statut.cle === k.etat) return statut;
		const cle: CleStatut = Object.hasOwn(LIBELLE_STATUT, k.etat) ? (k.etat as CleStatut) : 'manque';
		// Without its theme, a gap says the served reason of the figure (UI-SPEC v4 § 3.8), made readable.
		if (k.etat === 'manque' && k.motif) return { cle, texte: libelleCode(k.motif) };
		return { cle, texte: LIBELLE_STATUT[cle] };
	}

	const rang = (id: string): number => {
		const i = ORDRE_TYPES.indexOf(id);
		return i < 0 ? ORDRE_TYPES.length : i;
	};

	const lus = $derived(
		chiffres
			.filter((k) => !HORS_TETE.has(k.id))
			.map((k) => {
				const theme = themes.find((t) => t.id === k.theme);
				return { k, theme, libelle: textes?.get(`cle:${k.id}`) ?? k.libelle, figures: k.etat === 'ok' ? k.chiffres : [] };
			})
	);

	// Only an answered state carries figures (the API empties them otherwise). The DPE of several buildings
	// has no dominant class but its count is served: it answers, without a class.
	const aDesDpeSansClasse = (l: (typeof lus)[number]): boolean => l.figures.length === 0 && l.k.theme === 'dpe' && l.theme?.etat === 'ok' && !!l.theme.accroche;
	const repond = (l: (typeof lus)[number]): boolean => l.figures.length > 0 || l.k.etat === 'zero' || aDesDpeSansClasse(l);

	const tuiles = $derived(
		lus.filter(repond).map((l) => {
			const { k, theme, libelle, figures } = l;
			const classe = figures.find((c) => c.unite === 'classe_dpe' && /^[A-G]$/.test(String(c.valeur)));
			const repere = k.granularite ? niveauDe(k.granularite) : null;
			const repartition = theme ? aplatirDetails(theme.details).find((n) => n.type === 'repartition_dpe') : undefined;
			const alerte = theme?.rattachement?.confiance === 'sure' ? null : (theme?.rattachement?.alerte ?? null);
			const plage = theme?.chiffres.find((c) => c.unite === 'classe_dpe' && !/^[A-G]$/.test(String(c.valeur)));
			return {
				id: k.id,
				libelle,
				partie: partieDeTheme(k.theme),
				zero: k.etat === 'zero',
				// The figures, split around the no-break space so that the unit stays in the body face.
				valeurs: classe
					? []
					: [...figures]
							.sort((a, b) => rang(a.id) - rang(b.id))
							.map((c) => {
								const [nombre = '', ...unite] = valeurChiffre(c).split(NBSP);
								return { id: c.id, nombre, unite: unite.join(NBSP), precision: QUI[c.id] ?? (c.libelle !== k.libelle ? c.libelle : null) };
							}),
				classe: classe ? String(classe.valeur) : null,
				sousClasse: classe?.libelle ?? null,
				cases: classe && repartition?.type === 'repartition_dpe' ? repartition.classes : null,
				effectif: classe ? (theme?.accroche ?? null) : null,
				compte: aDesDpeSansClasse(l) ? theme!.accroche : null,
				plage: aDesDpeSansClasse(l) && plage ? `${plage.libelle}${NBSP}${String(plage.valeur)}` : null,
				phraseZero: k.etat === 'zero' ? typographieFr(theme?.verdict ?? statutSansChiffre(k, theme).texte) : null,
				lien: k.etat === 'zero' && k.theme === 'dpe' ? (lienCommune ?? null) : null,
				repere: repere ? { pictogramme: repere.pictogramme, mot: repere.mot } : undefined,
				alerte: alerte ? typographieFr(alerte) : null
			};
		})
	);

	// An answer that is not there keeps its rank: said before the tiles when the first figure is missing.
	const absencesAvant = $derived(lus.length > 0 && !repond(lus[0]));
	// One line per word of a state: two prices out of coverage are one line, never two empty boxes.
	const absences = $derived.by(() => {
		const parMot = new Map<string, { statut: Statut; libelles: string[]; phrases: string[] }>();
		for (const l of lus.filter((x) => !repond(x))) {
			const statut = statutSansChiffre(l.k, l.theme);
			const groupe = parMot.get(statut.texte) ?? { statut, libelles: [], phrases: [] };
			groupe.libelles.push(l.libelle);
			const phrase = l.theme ? typographieFr(l.theme.verdict) : null;
			if (phrase && !groupe.phrases.includes(phrase)) groupe.phrases.push(phrase);
			parMot.set(statut.texte, groupe);
		}
		return [...parMot.values()].map((g) => ({
			statut: g.statut,
			titres: g.libelles.join(' et '),
			phrase: [...g.phrases, ...(g.statut.cle === 'hors_couverture' ? [PAS_ZERO] : [])].join(' ')
		}));
	});
</script>

{#snippet pied(t: (typeof tuiles)[number])}
	{#if t.repere || t.effectif}
		<span class="cle-n"
			>{#if t.repere}<RepereEchelle pictogramme={t.repere.pictogramme} mot={t.repere.mot} taille={18} />{/if}{#if t.effectif}<span>{t.effectif}</span
				>{/if}</span
		>
	{/if}
{/snippet}

{#snippet lignesAbsentes()}
	{#if absences.length}
		<ul class="absences">
			{#each absences as a (a.statut.texte)}
				<li><BadgeEtat statut={a.statut} /> <span><strong>{a.titres}</strong>{#if a.phrase}{`${NBSP}: ${a.phrase}`}{/if}</span></li>
			{/each}
		</ul>
	{/if}
{/snippet}

<div class="bande" style:--n={tuiles.length}>
	{#if absencesAvant}{@render lignesAbsentes()}{/if}
	{#if tuiles.length}
		<dl class="chiffres-cles">
			{#each tuiles as t (t.id)}
				<div class={t.partie ? `cle teinte-${t.partie}` : 'cle'}>
					<dt>{#if t.partie}<IconeCategorie partie={t.partie} taille={18} />{/if}{t.libelle}</dt>
					<dd>
						{#if t.classe}
							<span class="dpe-tete">
								<span class="cle-v dpe-pastille dpe-{t.classe}">{t.classe}</span>
								<span class="dpe-texte">
									{#if t.sousClasse}<span class="cle-p">{t.sousClasse}</span>{/if}
									{@render pied(t)}
								</span>
							</span>
							{#if t.cases}
								<span class="dpe-bande" aria-hidden="true">
									{#each t.cases as c (c.classe)}
										<span class:dpe-dominante={c.classe === t.classe} class="dpe-{c.classe}">{c.classe}</span>
									{/each}
								</span>
							{/if}
						{:else if t.compte}
							<span class="cle-v">{t.compte}</span>
							{#if t.plage}<span class="cle-p">{t.plage}</span>{/if}
						{:else if t.phraseZero}
							<span class="cle-zero">{t.phraseZero}</span>
							{#if t.lien}<a class="cle-suite" href={t.lien}>Voir les DPE de la commune</a>{/if}
						{:else}
							<span class="valeurs">
								{#each t.valeurs as v (v.id)}
									<span class="valeur">
										<span class="cle-v">{v.nombre}{#if v.unite}<span class="cle-u">{NBSP}{v.unite}</span>{/if}</span>
										{#if v.precision}<span class="cle-p">{v.precision}</span>{/if}
									</span>
								{/each}
							</span>
						{/if}
						{#if !t.classe}{@render pied(t)}{/if}
						{#if t.alerte}<span class="cle-note">{t.alerte}</span>{/if}
						{#if t.id === TUILE_PRIX}{@render suitePrix?.()}{/if}
					</dd>
				</div>
			{/each}
		</dl>
	{/if}
	{#if !absencesAvant}{@render lignesAbsentes()}{/if}
	{#if suitePrix && !tuiles.some((t) => t.id === TUILE_PRIX)}<p class="bande-suite">{@render suitePrix()}</p>{/if}
</div>

<style>
	/* One tile per answer, on the ground of its category; what is not there stands apart, on paper. */
	.bande {
		display: grid;
		gap: var(--s3);
	}

	.chiffres-cles {
		display: grid;
		grid-template-columns: repeat(var(--n, 3), minmax(0, 1fr));
		gap: var(--s3);
		margin: 0;
	}

	.cle {
		position: relative;
		container-type: inline-size;
		min-width: 0;
		padding: var(--s3) var(--s4);
		border-radius: var(--radius-tuile);
		background: var(--c-fond, var(--color-paper));
	}

	dt {
		display: flex;
		align-items: center;
		gap: var(--s-demi);
		color: var(--c-encre, var(--color-muted));
		font-size: 14px;
		font-weight: 600;
		line-height: 1.4;
	}

	dd {
		display: grid;
		gap: var(--s2);
		margin: var(--s2) 0 0;
	}

	.valeurs {
		display: flex;
		flex-direction: column;
		gap: var(--s2);
	}

	/* Two figures side by side as soon as each keeps its unit on its line. */
	@container (min-width: 290px) {
		.valeurs {
			flex-direction: row;
			gap: var(--s4);
		}

		.valeur {
			flex: 1 1 0;
		}
	}

	.valeur {
		display: grid;
		gap: var(--s-fin);
		min-width: 0;
	}

	/* The value: Newsreader 30 (24 under 720 px), tabular figures, the whole figure on one line. */
	.cle-v {
		font-family: var(--font-display);
		font-size: 30px;
		font-weight: 500;
		line-height: 1.1;
		letter-spacing: -0.01em;
		font-variant-numeric: lining-nums tabular-nums;
		white-space: nowrap;
	}

	/* The unit stays on the line of its number, in the body face. */
	.cle-u {
		color: var(--color-muted);
		font-family: var(--font-sans);
		font-size: 14px;
		font-weight: 400;
		letter-spacing: 0;
	}

	.cle-p {
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.3;
	}

	.dpe-tete {
		display: flex;
		align-items: center;
		gap: var(--s3);
	}

	.dpe-texte {
		display: grid;
		gap: var(--s-fin);
		min-width: 0;
	}

	/* DPE class: the pastille takes its colour from the global `.dpe-A` to `.dpe-G`. */
	.dpe-pastille {
		flex: none;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 44px;
		height: 44px;
		border-radius: var(--radius-control);
	}

	/* The seven classes in one strip: the classes served at this address in their colour, the first one
	   outlined. Decoration of the sentence of the row, which says the same to everyone. */
	.dpe-bande {
		display: grid;
		grid-template-columns: repeat(7, minmax(0, 1fr));
		gap: var(--s-fin);
		max-width: 280px;
	}

	.dpe-bande span {
		display: flex;
		align-items: center;
		justify-content: center;
		height: 22px;
		border-radius: var(--radius-ligne);
		font-size: 14px;
		line-height: 1;
	}

	/* The seven classes keep the colour of the official label, whole from A to G (global `.dpe-A` to
	   `.dpe-G`). Only the most frequent class is marked, by a frame: a mark on every class served at
	   the address read as raised buttons, and the detail of the classes is in the DPE row below. */
	.dpe-bande span {
		color: var(--color-dpe-ink);
	}

	.dpe-bande :global(.dpe-A) {
		color: var(--color-dpe-sur-a);
	}

	.dpe-bande :global(.dpe-G) {
		color: var(--color-dpe-sur-g);
	}

	.dpe-bande .dpe-dominante {
		box-shadow: inset 0 0 0 2px var(--color-ink);
		font-weight: 600;
	}

	/* A served zero: the served sentence, in words, in the place of the value. */
	.cle-zero {
		color: var(--color-ink);
		font-family: var(--font-display);
		font-size: 20px;
		font-weight: 500;
		line-height: 1.3;
		text-wrap: balance;
	}

	.cle-suite {
		width: fit-content;
		color: var(--color-accent);
		font-size: 14px;
		line-height: 1.4;
		text-decoration-line: underline;
		text-decoration-thickness: 1px;
		text-underline-offset: 3px;
	}

	.cle-n {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--s-fin) var(--s2);
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
	}

	.cle-note {
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
		overflow-wrap: anywhere;
	}

	/* The answers that are not there: under the tiles, in the same object, one line per state. */
	.absences {
		margin: 0;
		padding: var(--s3) var(--s4);
		border: 1px solid var(--color-line);
		border-radius: var(--radius-tuile);
		background: var(--color-paper);
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
		list-style: none;
	}

	.bande-suite {
		margin: 0;
		font-size: 14px;
		line-height: 1.4;
	}

	.absences li {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: var(--s-demi) var(--s2);
	}

	.absences li + li {
		margin-top: var(--s2);
	}

	.absences strong {
		color: var(--color-ink);
		font-weight: 600;
	}

	.absences span {
		min-width: 0;
		flex: 1 1 12rem;
		overflow-wrap: anywhere;
	}

	@media (max-width: 720px) {
		.chiffres-cles {
			grid-template-columns: minmax(0, 1fr);
			gap: var(--s2);
		}

		.cle-v {
			font-size: 24px;
		}

		/* The first screen is short: the scale takes the end of the line of the label, the strip stays on the
		   wider screens, where it repeats no less than here what the row says. */
		.valeurs + .cle-n {
			position: absolute;
			top: var(--s3);
			right: var(--s4);
		}

		dd {
			gap: var(--s1);
			margin-top: var(--s1);
		}

		.dpe-bande {
			display: none;
		}

	}

	@media print {
		.cle {
			break-inside: avoid;
		}
	}
</style>
