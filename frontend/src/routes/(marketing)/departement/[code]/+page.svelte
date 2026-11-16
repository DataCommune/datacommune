<script lang="ts">
	// Departement page (04.7-10, decision 6; form of the direction A, UI-SPEC v4 § 6.3, plan 04.8-05):
	// the key figures of a departement, read from GET /v1/departements/{code}. The DOM order is the
	// reading order: the pale head band of the commune fiche (breadcrumb, title, the line under it:
	// departement, region, import sentence), the panel « L’essentiel » of four tiles « value, count, Détail »
	// in the order of the commune fiche (houses, flats, DPE classed F or G, then the population; D1, D2 of
	// 2026-11-10), the prices in the tint of the prices and the DPE in the tint of the housing; the
	// definition, the period and the exact base are in the panel of the tile, and on paper in the block of
	// definitions (phase 4.9, D3), the principal communes in a table, the ways to the communes and to
	// the other departements (page links, →), the sources (external links, ↗). Each card follows the state of its own block: a value, or the state served with its
	// reason; an absence is never a zero. The page reads the served model, it computes nothing.
	// Indexation is the API's (`indexable`): `noindex, follow` otherwise, and then no structured data.
	// An outage is rendered whole and answered 503 by hooks.server.ts (the load marks it).
	import { invalidateAll } from '$app/navigation';
	import ListeLiens, { type LienDeListe } from '$lib/components/ds/ListeLiens.svelte';
	import MetaPartage from '$lib/components/ds/MetaPartage.svelte';
	import Panneau from '$lib/components/ds/Panneau.svelte';
	import Tuile from '$lib/components/ds/Tuile.svelte';
	import Notice from '$lib/components/fiche/Notice.svelte';
	import SourceButton from '$lib/components/fiche/SourceButton.svelte';
	import SourcePanel from '$lib/components/fiche/SourcePanel.svelte';
	import StateValue from '$lib/components/fiche/StateValue.svelte';
	import { SITE_URL } from '$lib/config';
	import { groupesParRegion } from '$lib/data/departement';
	import {
		type LigneSource,
		annuaireParLettre,
		cartesEssentiel,
		descriptionMeta,
		donneesStructurees,
		filAriane,
		lienToutesLesCommunes,
		ligneSource,
		lignesCommunesPrincipales,
		phraseImportation,
		phraseListeVide,
		phraseTaille,
		titreCommunesPrincipales,
		voletCarte
	} from '$lib/data/departement-presentation';
	import { jsonLdScript } from '$lib/seo/json-ld';
	import { carteDepartement, urlImageOg } from '$lib/seo/og-carte';
	import { developperParCle, developperSigles } from '$lib/utils/sigles';
	import { typographieFr } from '$lib/utils/typographie';
	import { titreDepartement } from '$lib/seo/titres';
	import type { PageData } from './$types';
	import FilAriane from '$lib/components/ds/FilAriane.svelte';

	let { data }: { data: PageData } = $props();

	const synthese = $derived(data.etat === 'synthese' ? data.synthese : null);
	const nom = $derived(data.etat === 'synthese' ? data.synthese.nom : data.nom);
	const surtitre = $derived(
		data.etat === 'synthese' && data.synthese.region
			? `Département ${data.code} · ${data.synthese.region}`
			: `Département ${data.code}`
	);

	// Display order of « L'essentiel »; the structured data keep the order of `cartesDepartement`.
	const cartes = $derived(synthese ? cartesEssentiel(synthese) : []);
	// The tint of the category each tile answers, as the tiles of the commune fiche (`Kpi.svelte`).
	const TEINTES: Readonly<Record<string, string>> = { maison: 'q-prix', appartement: 'q-prix', dpe: 'q-logement' };
	const lignes = $derived(synthese ? lignesCommunesPrincipales(synthese) : []);
	const sources = $derived(
		data.etat === 'synthese' ? data.sources.map(ligneSource).filter((ligne): ligne is LigneSource => ligne !== null) : []
	);
	const etapes = $derived(filAriane(data.code, nom));
	const donnees = $derived(
		data.etat === 'synthese' ? donneesStructurees({ synthese: data.synthese, sources: data.sources, siteUrl: SITE_URL }) : []
	);
	// A source with an https link is an external link of the list (↗); one without is a plain line.
	const sourcesLiees = $derived(
		sources
			.filter((source) => source.href)
			.map((source): LienDeListe => ({ href: source.href!, titre: source.nom, note: source.detail.replace(/^·\s*/, '') || undefined }))
	);
	const sourcesSansLien = $derived(sources.filter((source) => !source.href));
	// Acronyms spelled out at their first occurrence (04.8-08, D-17, D-31): the prose of the main, in
	// reading order — each tile (label, then its short scope), then the sources (name and producer ·
	// licence). Never a value, a table cell, an attribute or the JSON-LD.
	// The other texts of the page hold no acronym of the list.
	const textes = $derived(
		developperParCle([
			...cartes.flatMap((carte) => [
				[`carte:${carte.id}:libelle`, carte.libelle] as const,
				[`carte:${carte.id}:note`, carte.portee] as const
			]),
			...sourcesLiees.flatMap((lien, index) => [
				[`source-liee:${index}:titre`, lien.titre] as const,
				[`source-liee:${index}:note`, lien.note] as const
			]),
			...sourcesSansLien.flatMap((source, index) => [
				[`source:${index}:nom`, source.nom] as const,
				[`source:${index}:detail`, source.detail] as const
			])
		])
	);
	// A panel is read alone: its acronyms are spelled out in it, whatever the page said before.
	const volets = $derived(
		synthese && data.etat === 'synthese'
			? cartes.map((carte) => {
					const volet = voletCarte(carte, synthese, data.sources);
					const textesVolet = developperSigles(volet.entrees.map((e) => ('valeur' in e ? typographieFr(e.valeur) : '')));
					return {
						carte,
						...volet,
						entrees: volet.entrees.map((e, i) => ('valeur' in e ? { ...e, valeur: textesVolet[i] } : e))
					};
				})
			: []
	);
	let panneaux = $state<Record<string, ReturnType<typeof SourcePanel> | undefined>>({});
	const sourcesLieesLues = $derived(
		sourcesLiees.map((lien, index) => ({
			...lien,
			titre: textes.get(`source-liee:${index}:titre`) ?? lien.titre,
			note: lien.note && (textes.get(`source-liee:${index}:note`) ?? lien.note)
		}))
	);
	// The other departements of the region, in the order of their codes; none when the list failed.
	const memeRegion = $derived.by(() => {
		if (data.etat !== 'synthese' || !data.departements || !data.synthese.region) return [];
		const groupe = groupesParRegion(data.departements).find((g) => g.region === data.synthese.region);
		return (groupe?.departements ?? []).filter((d) => d.code !== data.code);
	});
	const liensRegion = $derived(
		memeRegion.map((autre): LienDeListe => ({ href: `/departement/${autre.code}`, titre: `${autre.nom}\u00a0(${autre.code})` }))
	);

	// Every page of the departement, A to Z, folded (SEO audit 2026-11-14): each commune fiche gets a
	// link rendered by the server. Left out when the read failed or listed nothing.
	const annuaire = $derived(data.etat === 'synthese' && data.fiches ? annuaireParLettre(data.fiches) : []);
	const nombreFiches = $derived(annuaire.reduce((total, groupe) => total + groupe.fiches.length, 0));

	const pageTitle = $derived(titreDepartement(nom, data.code));
	const metaDescription = $derived(synthese ? descriptionMeta(synthese) : `Chiffres clés du département ${nom}.`);
	const indexable = $derived(synthese ? synthese.indexable : true);
	// The page's own link preview: its served figures, drawn by /og/departement/{code}.png (generic without).
	const carteOg = $derived(data.etat === 'synthese' ? carteDepartement(data) : null);
	const imageOg = $derived(carteOg ? { url: urlImageOg(SITE_URL, carteOg), alt: carteOg.alt } : null);

	// Typed failure of the outage state: a rate limit or a refused access is worded by the client;
	// null keeps the generic outage text (5xx, network).
	const messagePanne = $derived(data.etat === 'panne' ? (data.detail.message ?? null) : null);
	const titrePanne = $derived(data.etat === 'panne' ? (data.detail.titre ?? null) : null);

	// « Recharger les chiffres »: one reload in flight at most, announced by aria-busy.
	let rechargement = $state(false);
	async function recharger() {
		if (rechargement) return;
		rechargement = true;
		try {
			await invalidateAll();
		} finally {
			rechargement = false;
		}
	}
</script>

<!-- Title, description, canonical and the card of a shared link (T1): the tags of the commune fiche. -->
<MetaPartage
	titre={pageTitle}
	description={metaDescription}
	chemin={`/departement/${data.code}`}
	robots={indexable ? undefined : 'noindex, follow'}
	image={imageOg}
/>

<svelte:head>
	<!-- Structured data only for an indexed page that serves a figure (see donneesStructurees). -->
	{#each donnees as objet, index (index)}
		{@html jsonLdScript(objet)}
	{/each}
</svelte:head>

{#snippet definitions()}
	<h3>Définitions</h3>
	<dl>
		{#each volets as volet (volet.carte.id)}
			<dt>{volet.carte.libelle}</dt>
			{#each volet.entrees as entree (entree.terme)}
				{#if 'valeur' in entree && entree.valeur && (entree.terme === 'Définition' || entree.terme === 'Base')}<dd>{entree.valeur}</dd>{/if}
			{/each}
		{/each}
	</dl>
{/snippet}

<div class="dep gabarit">
	<div class="dep-tete">
		<FilAriane {etapes} />

		<h1 class="dep-title">{nom}</h1>
		<!-- The former overline is the first clause of the line under the title, then the import sentence. -->
		<p class="fiche-sous">
			<span class="dep-overline">{surtitre}</span>{#if synthese}{@const importation = phraseImportation(synthese)}{#if importation}{' · '}<span
						class="dep-import">{typographieFr(importation)}</span
					>{/if}{/if}
		</p>
	</div>

	{#if data.etat === 'synthese' && synthese}
		<Panneau variante="essentiel" titre="L’essentiel" id="chiffres">
			<dl class="cles">
				{#each cartes as carte (carte.id)}
					<Tuile libelle={textes.get(`carte:${carte.id}:libelle`) ?? carte.libelle} note={textes.get(`carte:${carte.id}:note`) ?? carte.portee ?? undefined}>
						{#snippet action()}
							<SourceButton forme="detail" libelle={carte.libelle} onouvrir={(declencheur) => panneaux[carte.id]?.ouvrir(declencheur)} />
						{/snippet}
						<div class="dep-valeur" data-carte={carte.id} data-teinte={TEINTES[carte.id] ?? null}><StateValue etat={carte.etat} taille="kpi" /></div>
					</Tuile>
				{/each}
			</dl>
			{#each volets as volet (volet.carte.id)}
				<SourcePanel bind:this={panneaux[volet.carte.id]} titre={volet.carte.libelle} entrees={volet.entrees} brut={volet.brut} source={volet.source} />
			{/each}
			<!-- Paper and a page without JavaScript have no panel: the explanations are written out for them (A9-9). -->
			<div id="definitions-impression" class="dep-impression dep-definitions">{@render definitions()}</div>
			<noscript><div class="dep-sans-js dep-definitions">{@render definitions()}</div></noscript>
		</Panneau>

		<Panneau titre={titreCommunesPrincipales(synthese)} id="communes-principales">
			{#if lignes.length}
				<!-- svelte-ignore a11y_no_noninteractive_tabindex (keyboard access is required for the scroll region) -->
				<div class="dep-tableau" role="region" tabindex="0" aria-label={titreCommunesPrincipales(synthese)}>
					<table class="dc-table">
						<!-- Read by assistive technologies and printed; off the screen, where it was 25 words in bold (D5). -->
						<caption class="dep-legende">
							Classées par population décroissante. Population du recensement de l’INSEE quand il est
							chargé, sinon celle du référentiel des communes, sans année.
						</caption>
						<thead>
							<tr>
								<th scope="col">{synthese.communes_principales.some((z) => z.type_zone === 'arrondissement') ? 'Arrondissement' : 'Commune'}</th>
								<th scope="col" class="num">Population</th>
							</tr>
						</thead>
						<tbody>
							{#each lignes as ligne (ligne.code)}
								<tr data-code-insee={ligne.code}>
									<th scope="row"><a href={ligne.href}>{ligne.nom}</a></th>
									<td class="num">
										<!-- The year and the source on their own line, as in the directory (T8): the « · » is read, not shown. -->
										{#if ligne.population}<span class="chiffre">{ligne.population.nombre}</span> {ligne.population.unite}{#if ligne.population.millesime}<span
													class="dep-millesime"><span class="dep-sr">{' · '}</span>{ligne.population.millesime}</span
												>{/if}{:else}Non renseignée{/if}
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{:else}
				<p class="dep-principales-vide">{typographieFr(phraseListeVide(synthese))}</p>
			{/if}
		</Panneau>

		{#if annuaire.length}
			<Panneau titre="Toutes les communes de A à Z" id="annuaire">
				<details class="det annuaire">
					<summary>Afficher les {nombreFiches} fiches du département</summary>
					{#each annuaire as groupe (groupe.lettre)}
						<h3 class="annuaire-lettre">{groupe.lettre}</h3>
						<ul class="annuaire-liste">
							{#each groupe.fiches as fiche (fiche.code)}
								<li><a href={fiche.href}>{fiche.nom}</a></li>
							{/each}
						</ul>
					{/each}
				</details>
			</Panneau>
		{/if}

		<Panneau titre="Parcourir" id="parcourir">
			{@const toutes = { href: lienToutesLesCommunes(synthese), titre: 'Toutes les communes', note: phraseTaille(synthese.communes) }}
			{@const tous = { href: '/departements', titre: 'Tous les départements' }}
			{#if memeRegion.length}
				<ListeLiens liens={[toutes]} />
				<h3 class="dep-h3">Autres départements de la région {synthese.region}</h3>
				<ListeLiens liens={liensRegion} />
				<ListeLiens liens={[tous]} />
			{:else}
				<!-- One list: a rule between its two rows (UI-SPEC v4 § 3.0; Opus 04.8-05-1, P3). -->
				<ListeLiens liens={[toutes, tous]} />
			{/if}
		</Panneau>

		{#if sources.length}
			<Panneau titre="Sources et licences" id="sources">
				{#if sourcesLiees.length}<ListeLiens cible="externe" liens={sourcesLieesLues} />{/if}
				{#if sourcesSansLien.length}
					<ul class="dep-sources">
						{#each sourcesSansLien as source, index (source.nom)}
							<li><span class="dep-source-nom">{textes.get(`source:${index}:nom`) ?? source.nom}</span>{#if source.detail}<span class="dep-source-detail"
										>{textes.get(`source:${index}:detail`) ?? source.detail}</span
									>{/if}</li>
						{/each}
					</ul>
				{/if}
			</Panneau>
		{/if}
	{:else}
		<div class="dep-etat">
			<Notice titre={titrePanne ?? 'Les chiffres de ce département sont momentanément indisponibles'} variante="warn" role="alert">
				<p>{messagePanne ?? 'Le service n’a pas répondu. Aucune valeur n’est affichée plutôt qu’une valeur incomplète.'}</p>
				<button type="button" class="bouton-secondaire" onclick={recharger} disabled={rechargement} aria-busy={rechargement}>Recharger les chiffres</button>
			</Notice>
		</div>
	{/if}
</div>

<style>
	/* A simple page of the direction A (UI-SPEC v4 § 6.0): `.gabarit`, h1 44/32, then panels. */
	.dep {
		padding-block: var(--s6) var(--s8);
		color: var(--color-ink);
	}

	/* The pale band of the head, as on the commune fiche: a shadow as wide as the window, cut to the block. */
	@media screen {
		.dep-tete {
			background: var(--color-bande);
			box-shadow: 0 0 0 100vmax var(--color-bande);
			clip-path: inset(calc(-1 * var(--s6)) -100vmax calc(-1 * var(--s4)));
		}
	}

	.dep-title {
		margin: var(--s4) 0 0;
		font-family: var(--font-display);
		font-size: 44px;
		font-weight: 500;
		line-height: 1.12;
		overflow-wrap: anywhere;
	}

	.fiche-sous {
		max-width: var(--mesure);
		margin: var(--s3) 0 0;
		color: var(--color-muted);
		font-size: 16px;
		line-height: 1.5;
		overflow-wrap: anywhere;
	}

	.dep-definitions {
		display: none;
		font-size: 14px;
		line-height: 1.4;
	}

	.dep-definitions h3 {
		margin: var(--s4) 0 var(--s2);
		font-size: 16px;
	}

	.dep-definitions dl {
		margin: 0;
	}

	.dep-definitions dt {
		margin-top: var(--s2);
		font-weight: 600;
	}

	.dep-definitions dd {
		margin: 0;
	}

	@media screen {
		.dep-sans-js {
			display: block;
		}
	}

	@media print {
		.dep-impression {
			display: block;
		}
	}

	.dep-valeur {
		min-width: 0;
		overflow-wrap: anywhere;
	}

	/* The tiles of the commune fiche (`Kpi.svelte`): the tint of the category the figure answers, its label in
	   the ink of that tint, the « i » in the corner of the tile and the label clear of it (D1, D3). A Tuile
	   takes no class: its value says its tint. */
	/* Each tile keeps the height of its own content: a tall neighbour (a state and its sentence) does not stretch
	   the others with nothing inside (D4). */
	.cles {
		align-items: start;
	}

	.cles :global(.cle) {
		position: relative;
	}

	.cles :global(.cle:has(> dd > .dep-valeur[data-teinte='q-prix'])) {
		--c-encre: var(--color-prix-encre);
		--c-fond: var(--color-prix-fond);
	}

	.cles :global(.cle:has(> dd > .dep-valeur[data-teinte='q-logement'])) {
		--c-encre: var(--color-logement-encre);
		--c-fond: var(--color-logement-fond);
	}

	.cles :global(.cle:has(> dd > .dep-valeur[data-teinte])) {
		border-color: var(--c-fond);
		background: var(--c-fond);
	}

	.cles :global(.cle:has(> dd > .dep-valeur[data-teinte]) > dt) {
		color: var(--c-encre);
	}

	.cles :global(.cle > dt) {
		padding-right: var(--s5);
	}

	.cles :global(.cle .source-button) {
		position: absolute;
		top: 0;
		right: 0;
		margin: 0;
	}

	/* In a key figure tile the state is a word, not a badge (UI-SPEC v4 § 3.2): Newsreader 20, muted, a
	   source that did not answer in danger. Its label, reason and sentence are StateValue's. */
	.dep-valeur :global(.state-badge) {
		padding: 0;
		border: 0;
		background: none;
		color: var(--color-muted);
		font-family: var(--font-display);
		font-size: 20px;
		font-weight: 500;
		line-height: 1.3;
	}

	.dep-valeur :global([data-state='temporary_error'] .state-badge) {
		color: var(--color-danger);
	}

	.dep-h3 {
		margin: var(--s5) 0 0;
		font-family: var(--font-display);
		font-size: 22px;
		font-weight: 500;
		line-height: 1.25;
	}

	/* Detail table (UI-SPEC v4 § 3.13): it scrolls in its own named region, never the page. */
	.dep-tableau {
		overflow-x: auto;
	}

	.dc-table {
		width: 100%;
		border-collapse: collapse;
		font-size: 14px;
		line-height: 1.4;
	}

	.dc-table caption {
		padding: 0 0 var(--s2);
		text-align: left;
	}

	@media screen {
		.dep-legende {
			position: absolute;
			width: 1px;
			height: 1px;
			overflow: hidden;
			clip-path: inset(50%);
			white-space: nowrap;
		}
	}

	.dc-table th,
	.dc-table td {
		padding: var(--s-demi) var(--s3) var(--s-demi) 0;
		border-top: 1px solid var(--color-filet-pale);
		text-align: left;
		vertical-align: middle;
		overflow-wrap: anywhere;
	}

	.dc-table thead th {
		border-top: 0;
		border-bottom: 1px solid var(--color-line);
		color: var(--color-muted);
		font-weight: 600;
	}

	.dc-table .num {
		padding-right: 0;
		text-align: right;
		font-variant-numeric: tabular-nums;
	}

	.dc-table tbody th {
		font-weight: 400;
	}

	.dep-millesime {
		display: block;
		color: var(--color-muted);
		white-space: nowrap;
	}

	.dep-sr {
		position: absolute;
		width: 1px;
		height: 1px;
		overflow: hidden;
		clip-path: inset(50%);
		white-space: nowrap;
	}

	/* A link in a cell is a link in running text: underlined, 1 px, 3 px offset (UI-SPEC v4 § 3.0);
	   44 px high, the target of the touch widths. */
	.dc-table tbody th a {
		display: inline-flex;
		align-items: center;
		min-height: 44px;
		color: var(--color-ink);
		text-decoration-line: underline;
		text-decoration-thickness: 1px;
		text-underline-offset: 3px;
	}

	.dc-table tbody th a:hover {
		text-decoration-thickness: 2px;
	}

	/* The A-Z directory: one fold, the letters as small heads, the names in running text. */
	.det > summary {
		display: flex;
		align-items: center;
		gap: var(--s2);
		min-height: 44px;
		padding: var(--s2) 0;
		color: var(--color-ink);
		font-weight: 600;
		line-height: 1.4;
		list-style: none;
		cursor: pointer;
	}

	.det > summary::-webkit-details-marker {
		display: none;
	}

	/* The chevron is drawn by CSS: it turns when the detail opens (as the questions of the commune fiche). */
	.det > summary::before {
		content: '';
		flex: none;
		width: 7px;
		height: 7px;
		border-right: 1.5px solid var(--color-accent);
		border-bottom: 1.5px solid var(--color-accent);
		transform: rotate(-45deg);
		transition: transform 0.15s ease-out;
	}

	.det[open] > summary::before {
		transform: rotate(45deg);
	}

	.annuaire-lettre {
		margin: var(--s4) 0 var(--s1);
		padding-bottom: var(--s1);
		border-bottom: 1px solid var(--color-filet-pale);
		color: var(--color-muted);
		font-size: 14px;
		font-weight: 600;
	}

	.annuaire-liste {
		display: flex;
		flex-wrap: wrap;
		gap: 0 var(--s4);
		margin: 0;
		padding: 0;
		list-style: none;
		font-size: 14px;
		line-height: 1.4;
	}

	/* A link in running text: underlined, 1 px, 3 px offset; 44 px high, the target of the touch widths. */
	.annuaire-liste a {
		display: inline-flex;
		align-items: center;
		min-height: 44px;
		color: var(--color-ink);
		overflow-wrap: anywhere;
		text-decoration-line: underline;
		text-decoration-thickness: 1px;
		text-underline-offset: 3px;
	}

	.annuaire-liste a:hover {
		text-decoration-thickness: 2px;
	}

	@media print {
		.annuaire {
			display: none;
		}
	}

	.dep-sources {
		margin: var(--s3) 0 0;
		padding: 0;
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
		list-style: none;
		overflow-wrap: anywhere;
	}

	/* The name and the detail (« · Producteur · Licence ») are two items of a flex row: the room between
	   them is a gap, never a space of the text, which a flex row drops (Opus P2-2). */
	.dep-sources li {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0 var(--s2);
		padding: var(--s3) 0;
	}

	.dep-sources li + li {
		border-top: 1px solid var(--color-filet-pale);
	}

	/* After the external links, the plain lines keep the rule between two rows. */
	:global(.liste-liens) + .dep-sources {
		margin-top: 0;
		border-top: 1px solid var(--color-filet-pale);
	}

	.dep-source-nom {
		color: var(--color-ink);
		font-size: 16px;
		font-weight: 600;
	}

	.dep-etat {
		margin-top: var(--s6);
	}

	@media (max-width: 720px) {
		.dep {
			padding-block: var(--s5) var(--s7);
		}

		.dep-title {
			font-size: 32px;
		}
	}

	@media print {
		.dep {
			padding: 0;
		}

		.dep-etat .bouton-secondaire {
			display: none;
		}

		.dep-tableau {
			overflow: visible;
		}
	}
</style>
