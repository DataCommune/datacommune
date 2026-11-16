<script lang="ts">
	// One dataset of « Données & méthode » in the catalogue (UI-SPEC « /datasets », D-40; phase 04.5
	// plan 11, B7, UI-017): a compact row — name, producer and state — anchored on the catalogue key
	// (`/datasets#<key>`), whose name links to the source (`href`, built by the page from the current
	// URL, `jeu` changed only). Chosen, the row is followed by the one rendering of its metadata,
	// DatasetDetail, shown directly under the row at every width, with no fold of its own and without its
	// name, producer and badge, which the row already shows (review of 09/11). A state
	// the page cannot word is refused, reported on the console once and shown as « Non servi ».
	// Review of 2026-11-11: the row says the freshness in plain text (« Importé le 31 oct. 2026 », « Lu en
	// direct ») and keeps a badge for the exceptions only — not qualified, out of coverage, not imported
	// yet — so that they stand out of the list.
	import { etatDuJeu, type DatasetPublic } from '$lib/api/datasets';
	import DatasetDetail, { badgeDuJeu } from './DatasetDetail.svelte';

	interface Props {
		jeu: DatasetPublic;
		/** Departements hosted by the instance (union served by `/v1/datasets`). */
		departementsInstance: string[];
		/** Link that chooses this dataset (`?…jeu=<key>#<key>`). */
		href: string;
		/** This dataset is the source read: its detail is mounted after the row. */
		choisi: boolean;
		/** Prose of the page with its acronyms spelled out (04.8-08), handed to the detail. */
		textes?: ReadonlyMap<string, string>;
		/** Heading level of the row: 4 under a group title of the catalogue. */
		niveau?: 3 | 4;
	}

	let { jeu, departementsInstance, href, choisi, textes, niveau = 3 }: Props = $props();

	const badge = $derived(badgeDuJeu(jeu));
	const etat = $derived(etatDuJeu(jeu));
	// Plain text for a dataset that is read; a badge only for what the reader must notice.
	const enTexte = $derived(etat.kind === 'heberge' && etat.statut !== 'non_importe');

	const dateCourte = new Intl.DateTimeFormat('fr-FR', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
	const importeLe = $derived.by(() => {
		const iso = jeu.provenance?.derniere_importation_reussie;
		if (!iso || etat.kind !== 'heberge' || etat.statut !== 'disponible') return null;
		const date = new Date(iso);
		return Number.isNaN(date.getTime()) ? null : dateCourte.format(date);
	});
	const titreId = $derived(`jeu-${jeu.nom}-titre`);

	$effect.pre(() => {
		if (etat.kind === 'inconnu') {
			console.error(`[DatasetCard] état de jeu sans libellé, non affiché : ${jeu.nom} (${etat.statut})`);
		}
	});
</script>

<article id={jeu.nom} class="dataset-ligne" class:dataset-ligne-choisie={choisi} aria-labelledby={titreId}>
	<svelte:element this={`h${niveau}`} id={titreId} class="dataset-ligne-titre">
		<a {href} aria-current={choisi ? 'true' : undefined} data-sveltekit-noscroll data-sveltekit-keepfocus
			><span class="dataset-ligne-nom">{jeu.provenance?.nom ?? jeu.nom}</span><span class="fleche" aria-hidden="true"
				>→</span
			></a
		>
	</svelte:element>
	<p class="dataset-ligne-meta">
		{#if jeu.provenance}<span class="dataset-ligne-producteur">{jeu.provenance.producteur}</span>{/if}
		{#if badge && enTexte}
			<span class="dataset-etat"><span class="dataset-etat-mot">{badge.texte}</span>{#if importeLe}{' '}le {importeLe}{/if}</span>
		{:else if badge}
			<span class="dataset-badge" class:dataset-badge-warn={badge.alerte}>{badge.texte}</span>
		{:else}
			<span class="dataset-unserved">Non servi</span>
		{/if}
	</p>
</article>
{#if choisi}
	<div class="dataset-ouvert">
		<DatasetDetail {jeu} {departementsInstance} {textes} libelleId={titreId} niveauBlocs={niveau === 4 ? 5 : 4} />
	</div>
{/if}

<style>
	/* A row of a list (UI-SPEC v4 § 3.0): no box of its own, a pale line between two rows (drawn by the
	   list), the name in ink with the accent arrow on the right; the source read is set on the soft fill. */
	.dataset-ligne {
		position: relative;
		box-sizing: border-box;
		max-width: 100%;
		padding: var(--s3) var(--s2);
		border-radius: var(--radius-ligne);
		color: var(--color-ink);
		font-size: 16px;
		line-height: 1.5;
		overflow-wrap: anywhere;
		scroll-margin-top: 96px;
	}

	.dataset-ligne-choisie {
		background: var(--color-soft);
	}

	.dataset-ligne-titre {
		margin: 0;
		font-size: 16px;
		font-weight: 600;
		line-height: 1.4;
	}

	.dataset-ligne-titre a {
		display: flex;
		align-items: center;
		gap: var(--s3);
		min-height: 44px;
		color: var(--color-ink);
		text-decoration: none;
	}

	.dataset-ligne-nom {
		flex: 1 1 auto;
		min-width: 0;
	}

	/* The whole row is the link (§ 3.0): the link's box is stretched over the row, producer and state
	   included; the row takes the soft fill on hover, the source read keeps its underline. */
	.dataset-ligne-titre a::after {
		content: '';
		position: absolute;
		inset: 0;
		border-radius: var(--radius-ligne);
	}

	.dataset-ligne:has(.dataset-ligne-titre a:hover) {
		background: var(--color-soft);
	}

	.dataset-ligne-titre a[aria-current='true'] .dataset-ligne-nom {
		text-decoration-line: underline;
		text-decoration-thickness: 1px;
		text-underline-offset: 3px;
	}

	.fleche {
		flex: none;
		color: var(--color-accent);
	}

	.dataset-ligne-meta {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--s1) var(--s2);
		margin: 0;
	}

	.dataset-ligne-producteur {
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
	}

	.dataset-badge {
		display: inline-block;
		max-width: 100%;
		box-sizing: border-box;
		padding: var(--s-fin) var(--s2);
		border: 1px solid var(--color-line-strong);
		border-radius: var(--radius-badge);
		background: var(--color-paper);
		color: var(--color-ink);
		font-size: 14px;
		font-weight: 600;
		line-height: 1.4;
	}

	.dataset-badge-warn {
		border-color: var(--color-warn);
		background: var(--color-warn-bg);
		color: var(--color-warn);
	}

	.dataset-unserved {
		color: var(--color-muted);
		font-size: 14px;
	}

	.dataset-etat {
		color: var(--color-ink);
		font-size: 14px;
		line-height: 1.4;
		font-variant-numeric: tabular-nums;
	}

	.dataset-ouvert {
		box-sizing: border-box;
		max-width: 100%;
		padding: var(--s2) var(--s2) var(--s3);
	}

	/* One line from a tablet up: the name on the left, producer and state on the right, the arrow at the
	   end of the row. */
	@media (min-width: 721px) {
		.dataset-ligne {
			display: grid;
			grid-template-columns: minmax(0, 5fr) minmax(0, 4fr);
			column-gap: var(--s4);
			align-items: center;
			padding-right: var(--s6);
		}

		.dataset-ligne-meta {
			justify-content: flex-end;
			column-gap: var(--s3);
			text-align: right;
		}

		/* The freshness of every row ends on the same edge and starts on the same one: a column to scan. */
		.dataset-etat {
			min-width: 11.5em;
		}

		.fleche {
			position: absolute;
			inset-block: 0;
			right: var(--s2);
			display: flex;
			align-items: center;
		}
	}
</style>
