<script lang="ts">
	// DPE document page (UI-SPEC v4 § 6.7, 04.8-04; LIEU-03, D-102, D-111). Breadcrumb, h1 « Diagnostic de
	// performance énergétique d'un {type servi} de {surface servie} m² », the line « Document DPE · N°
	// {numéro} · établi le … · valable jusqu'au … » (the source number as served, `tabular-nums`), then
	// the panel « Classes déclarées » (energy and climate on the A to G scale, the four landmarks as tiles,
	// the note on what a document is), the actions, the open panel of what the document declares
	// (`dl.tab`), and two folds: the address it is attached to by the source, its provenance and validity
	// (reading of the reference pages, 2026-11-10: P1, P2). The page renders the projected model of the server loader and
	// computes nothing: no date, no validity, no class, no range, no value per m² (D45-05). Consulting
	// a document attributes it to nothing: no dwelling, sale or commune datum is rendered here.
	// Reading grammar D (plan 04.5-12, 04.5-12-CONCEPTION.md): the essential first (identity line and
	// the four landmarks, ① and ② read in the served per-m² values), then the detail, where each served
	// value of « Ce que déclare le document » (the yearly totals included) opens its Source panel.
	// States (SEM-01): a refused request (409, 429, 401, 403) is titled by `panne.ts` on this page
	// (HTTP 200); an unknown or malformed number (404) and an outage (503) are thrown by the loader and
	// rendered by `+error.svelte` (2026-10-05, 04-DECISIONS-TEXTES).
	import { goto, invalidateAll } from '$app/navigation';
	import { navigating } from '$app/stores'; // Project convention: stores, never the state module.
	import EchelleDpe, { type CaseDpe, type ClasseDpe } from '$lib/components/ds/EchelleDpe.svelte';
	import ListeValeurs, { type ValeurDeListe } from '$lib/components/ds/ListeValeurs.svelte';
	import Panneau from '$lib/components/ds/Panneau.svelte';
	import KpiStrip from '$lib/components/fiche/KpiStrip.svelte';
	import LienLimites from '$lib/components/fiche/LienLimites.svelte';
	import Notice from '$lib/components/fiche/Notice.svelte';
	import SourceButton from '$lib/components/fiche/SourceButton.svelte';
	import SourcePanel from '$lib/components/fiche/SourcePanel.svelte';
	import StateValue from '$lib/components/fiche/StateValue.svelte';
	import { dateLongue, libelleSurface, libelleType } from '$lib/data/adresse-fiche';
	import type { EtatKpi } from '$lib/data/commune-kpi';
	import { LIBELLE_NON_AFFICHABLE } from '$lib/data/etats-libelles';
	import {
		CHARGEMENT_DOCUMENT,
		MESSAGE_PANNE_DOCUMENT,
		PHRASE_DIVERGENCE,
		RACINE_ARIANE,
		SURTITRE_DOCUMENT,
		TITRE_ADRESSE_BAN,
		TITRE_ADRESSE_DECLAREE,
		TITRE_CARACTERISTIQUES,
		TITRE_PANNE_DOCUMENT,
		badgesIdentite,
		caracteristiquesDocument,
		documentRemplace,
		filAriane,
		jeuxSources,
		lienAdresse,
		ligneIdentite,
		ligneStatut,
		methodeProvenance,
		modeleAdresse,
		reperesDocument,
		titreDocument
	} from '$lib/data/document-dpe';
	import { lienSelectionNature } from '$lib/data/commune-comparison-selection';
	import MetaPartage from '$lib/components/ds/MetaPartage.svelte';
	import { documentComparisonSelection } from '$lib/stores/comparison-selection.svelte';
	import type { DocumentPageData } from './+page.server';
	import FilAriane from '$lib/components/ds/FilAriane.svelte';
	import { developperParCle } from '$lib/utils/sigles';

	let { data }: { data: DocumentPageData } = $props();

	const issue = $derived(data.issue);
	const doc = $derived(issue.kind === 'ok' ? issue.document : null);
	// Client navigation to another document: the figures of this one are never shown as the next one's.
	const chargement = $derived(
		$navigating?.to?.route.id === '/(marketing)/dpe/[numero]' && $navigating.to.params?.numero !== data.numero
	);
	const ariane = $derived(doc ? filAriane(doc) : [RACINE_ARIANE, { libelle: titreDocument(data.numero), href: null }]);
	const identite = $derived(doc ? ligneIdentite(doc) : []);
	const badges = $derived(doc ? badgesIdentite(doc) : []);
	const reperes = $derived(doc ? reperesDocument(doc) : []);
	const caracteristiques = $derived(doc ? caracteristiquesDocument(doc) : []);
	const adresse = $derived(doc ? modeleAdresse(doc) : null);
	const voirAdresse = $derived(doc ? lienAdresse(doc) : null);
	const jeux = $derived(doc ? jeuxSources(doc) : []);
	const statut = $derived(doc ? ligneStatut(doc) : null);
	const remplace = $derived(doc ? documentRemplace(doc) : null);
	const dateImport = $derived(doc ? dateLongue(doc.provenance.date_import) : null);
	const pageTitle = $derived(`${titreDocument(data.numero)} — DataCommune`);
	// Description of the sharing card (T1). Proposed, to be validated by Ianis.
	const descriptionPartage = $derived(
		`Document ${titreDocument(data.numero)} : classes énergie et climat, dates de validité, adresse déclarée et provenance, avec leur source.`
	);
	// P7 (2026-11-10): the rows of the two folds are rows of the shared `ListeValeurs`; a value that carries
	// links is given as page content (the snippets below), `etat` saying it in plain text.
	const enTexte = (texte: string): ValeurDeListe['etat'] => ({ kind: 'valeur', texte, unite: null });
	const lignesRattachement = $derived.by<ValeurDeListe[]>(() => {
		if (!adresse) return [];
		const declaree: ValeurDeListe =
			adresse.declaree.kind === 'etat'
				? { id: 'adresse-declaree', libelle: TITRE_ADRESSE_DECLAREE, etat: adresse.declaree.etat, aide: false }
				: { id: 'adresse-declaree', libelle: TITRE_ADRESSE_DECLAREE, etat: enTexte(adresse.declaree.texte), contenu: valeurAdresseDeclaree };
		const libelleBan = textes.get('adresse:ban') ?? TITRE_ADRESSE_BAN;
		const ban = adresse.ban;
		const ligneBan: ValeurDeListe =
			ban.kind === 'retenue'
				? { id: 'adresse-ban', libelle: libelleBan, etat: enTexte(ban.libelle), contenu: valeurBanRetenue }
				: ban.kind === 'etat'
					? { id: 'adresse-ban', libelle: libelleBan, etat: ban.etat, aide: false }
					: ban.kind === 'texte'
						? { id: 'adresse-ban', libelle: libelleBan, etat: enTexte(textes.get('adresse:ban:texte') ?? ban.texte) }
						: { id: 'adresse-ban', libelle: libelleBan, etat: enTexte(LIBELLE_NON_AFFICHABLE), contenu: valeurProvisoire };
		return [declaree, ligneBan];
	});
	const lignesProvenance = $derived.by<ValeurDeListe[]>(() => {
		if (!doc) return [];
		const lignes: ValeurDeListe[] = [
			{ id: 'source', libelle: 'Source', etat: enTexte(['ADEME', ...jeux.map((jeu) => jeu.libelle)].join(' · ')), contenu: valeurSource }
		];
		if (dateImport) lignes.push({ id: 'importation', libelle: 'Dernière importation réussie', etat: enTexte(dateImport) });
		lignes.push({ id: 'methode', libelle: 'Méthode', etat: enTexte(methodeProvenance(doc.methode)) });
		if (statut) {
			lignes.push({
				id: 'statut',
				libelle: 'Statut',
				etat: enTexte(statut.parties.map((partie) => partie.texte).join('')),
				contenu: statut.provisoire ? valeurProvisoire : valeurStatut
			});
		}
		if (remplace) lignes.push({ id: 'document-remplace', libelle: 'Document remplacé', etat: enTexte(remplace.texte), contenu: valeurRemplace });
		return lignes;
	});

	// P2: « Provenance et validité » opens by itself when the status says more than « En vigueur ».
	const provenanceOuverte = $derived(doc ? doc.statut.code !== 'en_vigueur' : false);

	// « Comparer avec un autre DPE » (UI-SPEC point 2, 04-10, decision row 15 of 04-04): a link to
	// the comparator with this document, which works without JavaScript; with it, the document joins
	// the DPE selection of the tab (its own storage) and the comparator opens on that selection.
	// Preloaded on tap only, like the address page (04-DECISIONS-TEXTES row 48).
	const lienComparer = $derived(doc ? lienSelectionNature('dpe', [doc.numero], false) : '/comparer');

	function comparer(event: MouseEvent) {
		if (!doc || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
		event.preventDefault();
		const ajout = documentComparisonSelection.add(doc.numero);
		// A full selection is opened as kept, never replaced by this document alone (D-15 FRONT WR-03).
		const garder = ajout.ajoute || ajout.raison !== 'invalide';
		const numeros = garder ? documentComparisonSelection.codes : [doc.numero];
		void goto(lienSelectionNature('dpe', numeros, false));
	}

	let rechargement = $state(false);
	let panneauStatut = $state<ReturnType<typeof SourcePanel>>();

	// The h1 names the document by its served type and surface (UI-SPEC v4 § 6.7); a value the document
	// does not serve is left out, never guessed. The article agrees with the served type.
	const OBJETS: Readonly<Record<string, string>> = { maison: 'd’une maison', appartement: 'd’un appartement', immeuble: 'd’un immeuble' };
	const servie = (fieldId: string) =>
		[...(doc?.caracteristiques ?? []), doc?.reperes?.logement_decrit.type_batiment, doc?.reperes?.logement_decrit.surface].find(
			(valeur) => valeur?.field_id === fieldId && valeur.etat === 'value'
		) ?? null;
	const titre = $derived.by(() => {
		if (!doc || chargement) return titreDocument(data.numero);
		const type = servie('dpe.document.type_batiment');
		const surface = servie('dpe.document.surface_habitable');
		const typeServi = type?.etat === 'value' && typeof type.valeur === 'string' ? type.valeur.toLowerCase() : null;
		const surfaceServie = surface?.etat === 'value' && typeof surface.valeur === 'number' ? libelleSurface(surface.valeur) : null;
		const base = 'Diagnostic de performance énergétique';
		// No served type, no object: the title never names what the document does not (Opus 04-1, P2).
		if (!typeServi) return base;
		const objet = OBJETS[typeServi];
		if (objet) return `${base} ${objet}${surfaceServie ? ` de ${surfaceServie}` : ''}`;
		// A served type this page has no article for: its served word, never a guessed noun.
		return `${base} (${[libelleType(typeServi), surfaceServie].filter(Boolean).join(', ')})`;
	});

	// The note of « Classes déclarées » says what the document describes, by its served type (Opus 04-1,
	// P1): a dwelling for a house or a flat (UI-SPEC v4 § 6.7), the building for a building DPE, and
	// nothing it cannot know otherwise.
	const noteClasses = $derived.by(() => {
		const type = servie('dpe.document.type_batiment');
		const typeServi = type?.etat === 'value' && typeof type.valeur === 'string' ? type.valeur.toLowerCase() : null;
		if (typeServi === 'maison' || typeServi === 'appartement')
			return 'Ce document décrit un logement à une date, tel que l’a saisi le diagnostiqueur. Ce n’est pas une mesure du bâtiment entier.';
		if (typeServi === 'immeuble') return 'Ce document décrit un immeuble à une date, tel que l’a saisi le diagnostiqueur.';
		return 'Ce document décrit ce qu’a saisi le diagnostiqueur, à une date.';
	});

	// « Classes déclarées »: the served class of each label on the A to G scale, then its served value
	// per m² (C1); a class that is not served keeps its served state, never a guessed letter.
	type EtatAffiche = Exclude<EtatKpi, { kind: 'absence-non-declaree' }>;
	const affichable = (etat: EtatKpi | undefined): EtatAffiche | null => (etat && etat.kind !== 'absence-non-declaree' ? etat : null);
	const CASES: CaseDpe[] = (['A', 'B', 'C', 'D', 'E', 'F', 'G'] as ClasseDpe[]).map((classe) => ({ classe }));
	const classes = $derived(
		reperes
			.filter((repere) => repere.id === 'energie' || repere.id === 'climat')
			.map((repere) => {
				const [mesure] = repere.lignes;
				return {
					id: repere.id,
					nom: repere.id === 'energie' ? 'Énergie' : 'Climat',
					classe: (mesure?.etiquette ?? null) as ClasseDpe | null,
					etatClasse: affichable(repere.lignes.find((ligne) => ligne.id === `${repere.id}-classe`)?.etat),
					mesure: affichable(mesure?.etat)
				};
			})
	);

	// « Ce que déclare le document » as `dl.tab`: each served value with its Source panel (D45-08).
	const valeursDeclarees = $derived<ValeurDeListe[]>(
		caracteristiques.map((caracteristique) => ({
			id: caracteristique.fieldId,
			libelle: caracteristique.libelle,
			etat: caracteristique.etat,
			fieldId: caracteristique.fieldId,
			provisoire: caracteristique.provisoire,
			numerique: caracteristique.numerique,
			aide: false,
			volet: caracteristique.volet,
			brut: caracteristique.ligne.brut ? [caracteristique.ligne.brut] : [],
			sourcePanneau: caracteristique.source
		}))
	);

	// Acronyms spelled out at their first occurrence (04.8-08, D-17, D-31): the prose of the main, in
	// reading order — the title, the line under it, each landmark label (its source is in its panel since P4), the term of the BAN
	// address and its sentence, the source and its datasets. The title always says « Diagnostic de
	// performance énergétique », so DPE is never spelled out here. Never a value, a number, a badge (the
	// nature of the match, the status), an attribute or the breadcrumb.
	const textes = $derived(
		developperParCle([
			['titre', titre],
			['surtitre', SURTITRE_DOCUMENT],
			...(chargement ? [] : reperes).flatMap((repere) => [
				[`kpi:${repere.id}:libelle`, repere.libelle] as const
			]),
			['adresse:ban', adresse ? TITRE_ADRESSE_BAN : null],
			['adresse:ban:texte', adresse?.ban.kind === 'texte' ? adresse.ban.texte : null],
			['source', doc ? 'ADEME' : null],
			...jeux.map((jeu) => [`jeu:${jeu.url}`, jeu.libelle] as const)
		])
	);

	// Printing opens the two folds and gives each its state back afterwards (UI-SPEC v4 § 2.5, as the
	// commune fiche); the CSS of the socle already prints a closed fold where `::details-content` exists.
	const IMPRIMES = 'details.document-repli';
	let avantImpression: boolean[] | null = null;
	function ouvrirPourImpression() {
		// A second `beforeprint` (page.pdf, a print preview) must not record the opened state as the reader's.
		if (avantImpression) return;
		const details = [...document.querySelectorAll<HTMLDetailsElement>(IMPRIMES)];
		avantImpression = details.map((d) => d.open);
		for (const d of details) d.open = true;
	}
	function rendreApresImpression() {
		if (!avantImpression) return;
		const etats = avantImpression;
		document.querySelectorAll<HTMLDetailsElement>(IMPRIMES).forEach((d, index) => (d.open = etats[index] ?? false));
		avantImpression = null;
	}

	async function recharger() {
		rechargement = true;
		try {
			await invalidateAll();
		} finally {
			rechargement = false;
		}
	}
</script>

<svelte:window onbeforeprint={ouvrirPourImpression} onafterprint={rendreApresImpression} />

<!-- Values of the folds that carry links or a mark (P7): page content of `ListeValeurs`. -->
{#snippet valeurAdresseDeclaree()}
	{#if adresse && adresse.declaree.kind !== 'etat'}<span class="document-valeur document-adresse-texte">{adresse.declaree.texte}</span>{/if}
{/snippet}
{#snippet valeurBanRetenue()}
	{#if adresse?.ban.kind === 'retenue'}
		<span class="document-valeur"><a class="document-lien" href={adresse.ban.href}>{adresse.ban.libelle}</a></span>
		<span class="document-nature">{adresse.ban.nature}</span>
	{/if}
{/snippet}
{#snippet valeurProvisoire()}
	<span class="document-valeur" data-provisoire>{LIBELLE_NON_AFFICHABLE}</span>
{/snippet}
{#snippet valeurSource()}
	<span class="document-valeur"
		>{textes.get('source') ?? 'ADEME'}{#each jeux as jeu (jeu.url)}<span aria-hidden="true">{' · '}</span><a class="document-lien" href={jeu.url} rel="external noopener"
				>{textes.get(`jeu:${jeu.url}`) ?? jeu.libelle}</a
			>{/each}</span
	>
{/snippet}
{#snippet valeurStatut()}
	{#if statut}
		<span class="document-valeur"
			>{#each statut.parties as partie, index (index)}{#if partie.href}<a class="document-lien" href={partie.href}>{partie.texte}</a
					>{:else}{partie.texte}{/if}{/each}</span
		>
		{#if statut.phrase}
			<SourceButton libelle="statut du document" onouvrir={(declencheur) => panneauStatut?.ouvrir(declencheur)} />
		{/if}
	{/if}
{/snippet}
{#snippet valeurRemplace()}
	{#if remplace}<span class="document-valeur"><a class="document-lien" href={remplace.href}>{remplace.texte}</a></span>{/if}
{/snippet}

<MetaPartage
	titre={pageTitle}
	description={descriptionPartage}
	chemin={`/dpe/${encodeURIComponent(data.numero)}`}
	canonique={false}
	robots="noindex, follow"
/>

<div class="document gabarit">
	<FilAriane etapes={ariane.map((etape) => ({ nom: etape.libelle, href: etape.href }))} />

	<!-- P1 (2026-11-10): the header says what the document is and when it is valid, nothing more; the classes
	     come right after it, then the actions. -->
	<header class="document-header">
		<h1 class="document-title">{textes.get('titre') ?? titre}</h1>
		<!-- « Document DPE · N° {numéro} · Établi le … · valable jusqu'au … » (UI-SPEC v4 § 6.7): the former
		     overline is the first clause, the source number as served in tabular figures; the method is said
		     by « Provenance et validité » and, for an old document, by its badge (P4). -->
		<p class="document-sous">
			<span class="document-overline">{textes.get('surtitre') ?? SURTITRE_DOCUMENT}</span>
			<span aria-hidden="true">{' · '}</span><span class="document-numero">N°&nbsp;<span class="chiffre">{data.numero}</span></span>
			{#if doc && !chargement}
				<span class="document-identity">
					{#each identite as segment, index (index)}
						<span aria-hidden="true">{' · '}</span><span>{segment.texte}</span>
					{/each}
					{#each badges as badge (badge)}<span class="document-badge">{badge}</span>{/each}
				</span>
			{/if}
		</p>
	</header>

	{#if chargement}
		<div class="document-chargement" aria-busy="true">
			<Panneau variante="essentiel" titre={CHARGEMENT_DOCUMENT}>
				<KpiStrip kpis={null} />
			</Panneau>
		</div>
	{:else if doc}
		{#if reperes.length}
			<!-- P6: the tint of the « logement » category of the fiches, where the DPE is read. -->
			<Panneau variante="essentiel" titre="Classes déclarées" id="classes" teinte="q-logement">
				{#each classes as ligneClasse (ligneClasse.id)}
					<!-- The class and its scale; the value per m² is read once, in its tile with its « i » (P4). -->
					<div class="dpe-l" data-classe-declaree={ligneClasse.id}>
						<div class="dpe-lt">
							{ligneClasse.nom}&nbsp;:
							{#if ligneClasse.classe}classe {ligneClasse.classe}{:else if ligneClasse.etatClasse}<StateValue
									etat={ligneClasse.etatClasse}
									aide={false}
								/>{/if}
						</div>
						<EchelleDpe cases={CASES} active={ligneClasse.classe} grande />
					</div>
				{/each}
				<KpiStrip kpis={reperes} etiquette="Repères du document" {textes} />
				<p class="dc-note">{noteClasses}</p>
			</Panneau>
		{/if}

		<!-- Actions (UI-SPEC point 2), after the classes (P1): « Comparer avec un autre DPE » (primary),
		     « Voir l'adresse », as the buttons of the socle (T2). -->
		<div class="document-actions">
			<a class="bouton-primaire" href={lienComparer} onclick={comparer} data-sveltekit-preload-data="tap">Comparer avec un autre DPE</a>
			{#if voirAdresse}<a class="bouton-secondaire" href={voirAdresse}>Voir l'adresse</a>{/if}
		</div>

		{#if doc.disponibilite === 'disponible'}
			<Panneau titre={TITRE_CARACTERISTIQUES} id="declare" teinte="q-logement">
				<ListeValeurs valeurs={valeursDeclarees} />
			</Panneau>

			{#if adresse}
				<!-- P2, T3: a fold of the socle, closed; its h2 is the summary (as `fiche/Categorie`). -->
				<details class="depliant depliant-cadre document-repli" id="rattachement">
					<summary><h2 class="document-repli-titre">Adresse et rattachement</h2></summary>
					<ListeValeurs valeurs={lignesRattachement} />
					{#if adresse.divergente}<p class="document-text">{PHRASE_DIVERGENCE}</p>{/if}
					<!-- Fixed sentence of the UI-SPEC (point 5), written here once: consulting is not attributing. -->
					<p class="document-text document-rattachement">Ce document n'est rattaché à aucun logement précis.</p>
				</details>
			{/if}
		{/if}

		<!-- P2, T3: closed while the status only says « En vigueur »; any other status (expired, replaced,
		     retired, validity not determined) is a mention of validity and opens it. -->
		<details class="depliant depliant-cadre document-repli" id="provenance" open={provenanceOuverte}>
			<summary><h2 class="document-repli-titre">Provenance et validité</h2></summary>
			<ListeValeurs valeurs={lignesProvenance} />
			{#if statut?.phrase}
				<!-- The status sentence of the owner (retired document), read in its panel. -->
				<SourcePanel
					bind:this={panneauStatut}
					titre="Statut"
					entrees={[{ terme: statut.parties.map((partie) => partie.texte).join(''), valeur: statut.phrase }]}
					brut={[]}
				/>
			{/if}
		</details>
	{:else if issue.kind === 'panne'}
		<div class="document-etat">
			<!-- A typed refusal keeps the title of panne.ts; an outage is rendered by +error.svelte. -->
			<Notice titre={issue.panne.titre ?? TITRE_PANNE_DOCUMENT} variante="warn" role="alert">
				<p>{issue.panne.message ?? MESSAGE_PANNE_DOCUMENT}</p>
				<button type="button" class="bouton-secondaire" onclick={recharger} disabled={rechargement} aria-busy={rechargement}
					>Recharger le document</button
				>
			</Notice>
		</div>
	{/if}

	<!-- « Ce que nous ne montrons pas » ends the page, as on the address fiche (P1). -->
	<div class="document-limites"><LienLimites /></div>
</div>

<style>
	/* A simple page of the direction A (UI-SPEC v4 § 6.0, § 6.7): `.gabarit`, h1 44/32. */
	.document {
		/* The page padding of `main` (UI-SPEC v4 § 2.3), as the address fiche writes it. */
		padding-block: var(--s6) var(--s8);
		color: var(--color-ink);
	}

	.document-title {
		margin: var(--s4) 0 0;
		font-family: var(--font-display);
		font-size: 44px;
		font-weight: 500;
		line-height: 1.12;
		overflow-wrap: anywhere;
	}

	.document-sous {
		max-width: var(--mesure);
		margin: var(--s2) 0 0;
		color: var(--color-muted);
		font-size: 16px;
		line-height: 1.6;
		overflow-wrap: anywhere;
	}

	.chiffre {
		font-variant-numeric: tabular-nums;
	}

	.document-numero {
		white-space: nowrap;
	}

	/* Neutral facts on a document, never a `warn` (UI-SPEC « Couleur »): the badge of § 3.6. */
	.document-badge {
		display: inline-block;
		margin-left: var(--s2);
		padding: var(--s1) var(--s2);
		border: 1px solid var(--color-line-strong);
		border-radius: var(--radius-badge);
		background: var(--color-soft);
		color: var(--color-ink);
		font-size: 14px;
		font-weight: 600;
		line-height: 1;
	}

	/* The actions, after the classes (P1); the buttons are the socle's (T2). */
	.document-actions {
		display: flex;
		flex-wrap: wrap;
		gap: var(--s2);
		margin: var(--s5) 0 0;
	}

	/* The two folds (P2, T3): spaced as the panels, the h2 in the summary at the size of a panel title. */
	.document-repli {
		margin-top: var(--s6);
	}

	.document-repli > summary {
		padding-block: var(--s4);
	}

	.document-repli-titre {
		margin: 0;
		color: var(--color-ink);
		font-family: var(--font-display);
		font-size: 30px;
		font-weight: 500;
		line-height: 1.2;
		letter-spacing: -0.01em;
	}

	.document-limites {
		margin-top: var(--s5);
	}

	/* The measure is the paragraph's own (60ch of its 14 px), as in the header it comes from. */
	.document-limites :global(.lien-limites) {
		max-width: var(--mesure);
	}

	/* « Énergie : classe X », the A to G scale, the served value per m² (`style.css:284-290`). */
	.dpe-l {
		display: grid;
		grid-template-columns: 90px auto minmax(0, 1fr);
		align-items: center;
		gap: var(--s2) var(--s4);
		margin-top: var(--s4);
		padding: var(--s3) 0;
	}

	/* A pale line between the two rows (`style.css:307-308`). */
	.dpe-l + .dpe-l {
		margin-top: 0;
		border-top: 1px solid var(--color-filet-pale);
	}

	.dpe-lt {
		color: var(--color-ink);
		font-size: 16px;
		font-weight: 600;
		line-height: 1.4;
	}

	.dpe-lt :global(.state-absence) {
		display: inline-flex;
	}

	.dc-note {
		max-width: var(--mesure);
		margin: 0;
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
	}

	/* A value of the folds given as page content (P7): the face, weight and size of a value of
	   `ListeValeurs` (`.tab-v`, `StateValue .value`), so the rows of one list read alike. */
	.document-valeur {
		font-family: var(--font-display);
		font-weight: 500;
		font-size: 20px;
		line-height: 1.3;
		overflow-wrap: anywhere;
	}

	/* A declared address, however long, is rendered whole and wraps (UI-SPEC « long-text · dpe »). */
	.document-adresse-texte {
		overflow-wrap: anywhere;
	}

	.document-nature {
		display: block;
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
	}

	/* A link in a sentence is underlined, 1 px, 3 px offset (UI-SPEC v4 § 3.0), in the accent of the charter
	   (P6, as `.tab-lien` of ListeValeurs). */
	.document-lien {
		color: var(--color-accent);
		text-decoration: underline;
		text-decoration-thickness: 1px;
		text-underline-offset: 3px;
		overflow-wrap: anywhere;
	}

	.document-lien:hover {
		color: var(--color-accent-hover);
		text-decoration-thickness: 2px;
	}

	.document-text {
		max-width: var(--mesure);
		font-size: 16px;
		line-height: 1.5;
		overflow-wrap: anywhere;
	}

	.document-etat {
		margin-top: var(--s6);
	}

	@media (max-width: 720px) {
		.document {
			padding-block: var(--s5) var(--s7);
		}

		.document-title {
			font-size: 32px;
		}

		.document-repli-titre {
			font-size: 26px;
		}

		.dpe-l {
			grid-template-columns: minmax(0, 1fr);
			gap: var(--s2);
		}

	}

	@media print {
		.document-actions,
		.document-limites {
			display: none;
		}

		.dpe-l {
			break-inside: avoid;
		}
	}
</style>
