<script lang="ts">
	// Home page = the single search of the site, communes and addresses (D-38, D-105, D-106;
	// 04-UI-SPEC « / — recherche unique »). Prerendered: nothing is fetched at load time and no
	// coverage counter is frozen in markup. Without JavaScript, the `CommuneSearch` form is a plain
	// `GET /communes` with `q`; `check-marketing-prerender.mjs` verifies it in the prerendered HTML.
	// No departement filter here (Ianis, 09/11): the results page keeps it for refining. A legacy `/bien/{slug}` link not resolved to one address lands here as
	// `/?q={label}` (04-08): the field reads it once mounted, without searching (`prefillFromUrl`).
	// Charter of 06/11 (Ianis's choices, amends § 6.6): a solid head centred on the field, rings of scale around
	// it, the facts of one real fiche dated around them, then that fiche as an example, the uses, the scales,
	// the method and the landmarks. The example is a frozen reading, said as such: its figures never pass for a
	// count of what the site covers (D-34). No line of promise (D-16).
	// Three examples since 11/11 (Ianis): a large city, a mid-sized town, a village, chosen by three buttons.
	// The choice is the only state of the page and asks nothing of the network. What differs in length from
	// one example to the next (its title, tiles and rows, the sentence of the head, the link to the commune) is rendered three
	// times in one grid cell, the two others hidden and inert: the cell is as tall as the tallest, so the page
	// never moves. The first example is the one of the prerendered HTML; without JavaScript the buttons hide.
	// A hidden copy is `inert` and not painted (`visibility: hidden`): both take it out of the tree a screen
	// reader reads, so no `aria-hidden`, which Svelte refuses on a heading.
	import { onMount } from 'svelte';
	import CommuneSearch from '$lib/components/CommuneSearch.svelte';
	import BadgeEtat from '$lib/components/ds/BadgeEtat.svelte';
	import IconeCategorie from '$lib/components/ds/IconeCategorie.svelte';
	import RepereEchelle from '$lib/components/ds/RepereEchelle.svelte';
	import Tuile from '$lib/components/ds/Tuile.svelte';
	import SourceButton from '$lib/components/fiche/SourceButton.svelte';
	import SourcePanel from '$lib/components/fiche/SourcePanel.svelte';
	import { OG_IMAGE_URL, SITE_URL } from '$lib/config';
	import { DATE_RELEVE_REPERES, ECHELLES_ACCUEIL, EXEMPLES_FICHES, REPERES_NATIONAUX, USAGES } from '$lib/data/accueil';
	import { LIBELLE_STATUT, type CleStatut } from '$lib/data/statut';
	import { NIVEAUX } from '$lib/data/echelles';
	import { QUESTIONS, type IdPartie } from '$lib/data/categories';
	import { EXEMPLES_ACCUEIL } from '$lib/data/recherche-lieux';
	import { jsonLdScript } from '$lib/seo/json-ld';
	import { developperParCle } from '$lib/utils/sigles';

	let choisi = $state(0);
	const exemple = $derived(EXEMPLES_FICHES[choisi]);
	// Doubled class: it outranks the scoped rule of the group.
	const STYLE_SANS_SCRIPT = '<noscript><style>.choix-exemple.choix-exemple{display:none}</style></noscript>';
	const TITLE = 'DataCommune — rechercher une commune ou une adresse';
	const DESCRIPTION =
		'Rechercher une commune ou une adresse, puis consulter les prix de vente, les documents DPE et les données locales disponibles.';

	// The page is its own canonical (04.7-10, decision 8): `/?q=…` (legacy `/bien` link) is a reading
	// state of the same page. The WebSite names the site; its SearchAction is the GET form below
	// (`/communes`, field `q`), written with absolute URLs. Google no longer draws a search box from
	// it (since November 2024): it is kept for the site name and for other readers.
	const CANONICAL_URL = `${SITE_URL}/`;
	const WEB_SITE = {
		'@context': 'https://schema.org',
		'@type': 'WebSite',
		name: 'DataCommune',
		url: CANONICAL_URL,
		inLanguage: 'fr-FR',
		potentialAction: {
			'@type': 'SearchAction',
			target: `${SITE_URL}/communes?q={search_term_string}`,
			'query-input': 'required name=search_term_string'
		}
	};

	// The legend has its link under the scale marks: the entry would say it a second time (4.9 lot 9).
	const ENTREES = EXEMPLES_ACCUEIL.filter((exemple) => exemple.href !== '/datasets#lire');
	const ETATS: readonly { cle: CleStatut; phrase: string }[] = [
		{ cle: 'zero', phrase: 'Lu à la source : il n’y en a aucun.' },
		{ cle: 'manque', phrase: 'La source existe, la valeur manque.' },
		{ cle: 'hors_couverture', phrase: 'Ce territoire n’est pas couvert. Cela ne vaut pas zéro.' },
		{ cle: 'panne', phrase: 'La source ne répond pas pour l’instant.' }
	];

	const TITRE_PARTIE = (id: IdPartie) => QUESTIONS.find((partie) => partie.id === id)?.titre ?? '';
	// A served result is written with its figures in bold: « de **11,35** à **18,54 €** ».
	const morceaux = (texte: string) => texte.split('**').map((t, i) => ({ t, fort: i % 2 === 1 }));

	const CHAPO = 'Chaque chiffre vient d’une source publique et dit jusqu’où il vaut : le point de l’adresse, le quartier ou toute la commune.';
	const PORTEE =
		'Une valeur de commune ne dit rien du point exact. Ce repère reste à côté de chaque chiffre, du plus précis au plus large.';
	const METHODE_SOURCES =
		'Les données viennent de producteurs publics : DGFiP pour les ventes, Ademe pour les documents DPE, INSEE, Géorisques, cadastre. Une valeur affichée porte sa période, sa source et, quand il existe, le nombre d’observations qui la fondent.';
	const METHODE_ETATS =
		'Une valeur nulle, une donnée manquante, un territoire hors couverture et un service en panne sont affichés différemment. DataCommune ne calcule aucun score global.';
	const PROFESSIONNELS = 'Professionnels de l’immobilier, collectivités : les mêmes fiches, période et source à l’appui.';
	// Acronyms spelled out at their first occurrence (04.8-08, D-17, D-31): the prose of the main in
	// reading order, from constants only, so the prerendered HTML and the hydration get the same text.
	// The facts and the tile of the example say « diagnostic énergétique »: DPE stays bare on this page.
	// Never a value of a landmark, its scope, its source link, an entry label or a scale word.
	const TEXTES = developperParCle([
		['chapo', CHAPO],
		...EXEMPLES_FICHES.flatMap((e) => e.faits.map((fait, i) => [`fait:${e.id}:${i}`, fait.libelle] as const)),
		...USAGES.map((usage) => [`usage:${usage.titre}`, usage.texte] as const),
		['professionnels', PROFESSIONNELS],
		['portee', PORTEE],
		...ECHELLES_ACCUEIL.map((entree) => [`echelle:${entree.niveau}`, entree.phrase] as const),
		['methode:sources', METHODE_SOURCES],
		['methode:etats', METHODE_ETATS],
		...REPERES_NATIONAUX.map((repere, i) => [`repere:${i}`, repere.libelle] as const)
	]);

	// A landmark reads as its figure and what it counts: its scope and its publication are behind the « i ».
	let panneauxReperes = $state<(ReturnType<typeof SourcePanel> | undefined)[]>([]);

	// The rings are centred on the field, whose height depends on the width and on what the filter shows.
	let hero = $state<HTMLElement>();
	let champ = $state<HTMLElement>();
	onMount(() => {
		if (!hero || !champ || typeof ResizeObserver === 'undefined') return;
		const zone = champ;
		const cadre = hero;
		const centrer = () => {
			const saisie = zone.querySelector('.cs-saisie') ?? zone;
			const boite = saisie.getBoundingClientRect();
			cadre.style.setProperty('--centre-anneaux', `${boite.top + boite.height / 2 - cadre.getBoundingClientRect().top}px`);
		};
		const observateur = new ResizeObserver(centrer);
		observateur.observe(cadre);
		centrer();
		return () => observateur.disconnect();
	});
</script>

<svelte:head>
	<title>{TITLE}</title>
	<meta name="description" content={DESCRIPTION} />
	<link rel="canonical" href={CANONICAL_URL} />
	<meta property="og:type" content="website" />
	<meta property="og:site_name" content="DataCommune" />
	<meta property="og:title" content={TITLE} />
	<meta property="og:description" content={DESCRIPTION} />
	<meta property="og:url" content={CANONICAL_URL} />
	<meta property="og:image" content={OG_IMAGE_URL} />
	<meta property="og:locale" content="fr_FR" />
	<meta name="twitter:card" content="summary_large_image" />
	<meta name="twitter:title" content={TITLE} />
	<meta name="twitter:description" content={DESCRIPTION} />
	<meta name="twitter:image" content={OG_IMAGE_URL} />
	{@html jsonLdScript(WEB_SITE)}
</svelte:head>

<div class="accueil">
	<section class="hero pleine-largeur" aria-labelledby="home-title" bind:this={hero}>
		<!-- The three circles of scale the page is built on: around the point, what a value covers. -->
		<div class="cadre-anneaux" aria-hidden="true">
			<svg class="anneaux" viewBox="-950 -950 1900 1900" focusable="false">
				<circle r="860" fill="var(--color-hero-anneau)" opacity=".55" />
				<circle r="620" fill="var(--color-hero-anneau)" />
				<circle r="410" fill="var(--color-hero-anneau-2)" />
				{#each [860, 620, 410] as rayon (rayon)}
					<circle r={rayon} fill="none" stroke="var(--color-hero-trait)" stroke-width="1.25" stroke-dasharray="2 7" stroke-linecap="round" />
				{/each}
				<!-- One line, at the height of the gap between the first two facts: nothing covers a label. -->
				<text x="-392" y="-120" text-anchor="middle">500 m</text>
				<text class="loin" x="-608" y="-120" text-anchor="middle">1 km</text>
				<text class="loin" x="-851" y="-120" text-anchor="middle">Commune</text>
			</svg>
		</div>
		<div class="gabarit hero-int">
			<h1 id="home-title">Une adresse, en clair</h1>
			<p class="acc-p">{TEXTES.get('chapo') ?? CHAPO}</p>
			<div class="acc-champ" bind:this={champ}>
				<CommuneSearch variante="grand" surAplat prefillFromUrl sansDepartement />
			</div>
			<p class="hero-suite">Pas d’adresse en tête&nbsp;? <a href="/communes">Parcourez l’annuaire des communes</a></p>
			<div class="note-exemple pile" data-exemple>
				{#each EXEMPLES_FICHES as e, i (e.id)}
					<p class:cache={i !== choisi} inert={i !== choisi}>Autour du champ, un exemple réel&nbsp;: {e.adresse}, lu le {e.date}.</p>
				{/each}
			</div>
		</div>
		<ul class="faits" data-exemple>
			{#each exemple.faits as fait, i (i)}
				<li class="fait fait-{i + 1}">
					<RepereEchelle pictogramme={fait.pictogramme} mot="" motVisible={false} />
					<div>
						<b>{fait.valeur}</b>
						<span>{TEXTES.get(`fait:${exemple.id}:${i}`) ?? fait.libelle}</span>
					</div>
				</li>
			{/each}
		</ul>
	</section>

	<div class="gabarit acc-suite">
		{@html STYLE_SANS_SCRIPT}
		<section class="exemple" aria-labelledby="ex-titre" data-exemple>
			<div class="choix-exemple" role="group" aria-label="Choisir l’exemple">
				{#each EXEMPLES_FICHES as e, i (e.id)}
					<button type="button" class="choix-bouton" aria-pressed={i === choisi} onclick={() => (choisi = i)}
						><span class="choix-profil">{e.profil}</span><span class="choix-sep">{' · '}</span><span class="choix-ville">{e.ville}</span></button
					>
				{/each}
			</div>
			<!-- What reads the same for each example is written once; what differs in length is stacked. -->
			<header class="ex-tete">
				<div>
					<span class="etiquette">Exemple</span>
					<div class="pile">
						{#each EXEMPLES_FICHES as e, i (e.id)}
							<h2 id={i === choisi ? 'ex-titre' : undefined} class:cache={i !== choisi} inert={i !== choisi}>{e.adresse}</h2>
						{/each}
					</div>
					<p>Une fiche réelle, lue le {exemple.date}. Ces chiffres sont figés&nbsp;: ouvrez la fiche pour tout voir.</p>
				</div>
				<a class="acc-bouton" href={exemple.href}>Ouvrir cette fiche <span class="fleche" aria-hidden="true">→</span></a>
			</header>
			<div class="pile">
				{#each EXEMPLES_FICHES as e, i (e.id)}
					<div class="ex-corps" class:cache={i !== choisi} inert={i !== choisi}>
						<div class="ex-chiffres">
							{#each e.tuiles as tuile (tuile.libelle)}
								<div class="mini teinte-{tuile.partie}">
									<p class="mini-l">{tuile.libelle}</p>
									<div class="mini-v">
										{#each tuile.valeurs as v (v.qui)}
											<div>
												<b class="chiffre">{v.valeur}</b>
												<span>{v.qui}</span>
											</div>
										{/each}
										{#if tuile.classe}
											<div class="mini-dpe">
												<span class="dpe-lettre dpe-{tuile.classe.lettre}">{tuile.classe.lettre}</span>
												<span>{tuile.classe.sous}<br />{tuile.classe.note}</span>
											</div>
										{/if}
									</div>
									<RepereEchelle pictogramme={NIVEAUX[tuile.echelle].pictogramme} mot={NIVEAUX[tuile.echelle].mot} taille={18} />
								</div>
							{/each}
						</div>
						<ul class="rangees">
							{#each e.lignes as ligne (ligne.partie)}
								<li>
									<a class="rangee teinte-{ligne.partie}" href={`${e.href}#${ligne.partie}`}>
										<span class="rangee-ico"><IconeCategorie partie={ligne.partie} /></span>
										<span class="rangee-nom">{TITRE_PARTIE(ligne.partie)}</span>
										<span class="rangee-res"
											>{#each morceaux(ligne.resume) as m, j (j)}{#if m.fort}<strong>{m.t}</strong>{:else}{m.t}{/if}{/each}</span
										>
										<span class="rangee-compte">
											{#if ligne.vigilances}<BadgeEtat statut={{ cle: 'vigilance', texte: `${ligne.vigilances} ${ligne.vigilances > 1 ? 'vigilances' : 'vigilance'} à l’adresse ou autour` }} />{/if}
										</span>
										<span class="fleche" aria-hidden="true">→</span>
									</a>
								</li>
							{/each}
						</ul>
					</div>
				{/each}
			</div>
		</section>

		<section class="acc-section" aria-labelledby="home-examples-title">
			<h2 id="home-examples-title">Pour commencer</h2>
			<ul class="usages">
				{#each USAGES as usage (usage.titre)}
					<li>
						<a class="carte-lien" href={`${exemple.href}${usage.ancre}`}>
							<h3>{usage.titre}</h3>
							<span class="usage-texte">{TEXTES.get(`usage:${usage.titre}`) ?? usage.texte}</span>
							<span class="usage-pied">{usage.action}<span class="fleche" aria-hidden="true">→</span></span>
						</a>
					</li>
				{/each}
			</ul>
			<ul class="entrees">
				{#each ENTREES as exemple (exemple.href)}
					<li>
						<a class="carte-lien" href={exemple.href}
							><span><span class="ex-t">{exemple.label}</span><span class="ex-n">{exemple.note}</span></span><span class="fleche" aria-hidden="true">→</span></a
						>
					</li>
				{/each}
			</ul>
			<p class="pros">{TEXTES.get('professionnels') ?? PROFESSIONNELS}
				<span class="pile-lien"
					>{#each EXEMPLES_FICHES as e, i (e.id)}<a href={e.commune} class:cache={i !== choisi} inert={i !== choisi}>Ouvrir la fiche de {e.ville}</a>{/each}</span
				>
			</p>
		</section>
	</div>

	<section class="acc-bande pleine-largeur" aria-labelledby="home-echelle-title">
		<div class="gabarit">
			<h2 id="home-echelle-title">Chaque chiffre dit jusqu’où il vaut</h2>
			<p class="section-p">{TEXTES.get('portee') ?? PORTEE}</p>
			<ol class="portees" style:--rond-max={`${36 + (ECHELLES_ACCUEIL.length - 1) * 6}px`}>
				{#each ECHELLES_ACCUEIL as entree, i (entree.niveau)}
					<li class="portee">
						<span class="portee-rond" style:width={`${36 + i * 6}px`} style:height={`${36 + i * 6}px`}>
							<RepereEchelle pictogramme={NIVEAUX[entree.niveau].pictogramme} mot="" motVisible={false} />
						</span>
						<h3>{NIVEAUX[entree.niveau].mot}</h3>
						<p>{TEXTES.get(`echelle:${entree.niveau}`) ?? entree.phrase}</p>
					</li>
				{/each}
			</ol>
			<p class="acc-lien"><a href="/datasets#lire">Comment lire une fiche : les échelles et les états</a></p>
		</div>
	</section>

	<div class="gabarit">
		<section class="acc-section deux" aria-labelledby="home-sources-title">
			<div>
				<h2 id="home-sources-title">D’où viennent les chiffres</h2>
				<p>{TEXTES.get('methode:sources') ?? METHODE_SOURCES}</p>
				<p>{TEXTES.get('methode:etats') ?? METHODE_ETATS}</p>
				<ul class="home-method-links">
					<li><a href="/datasets#couverture-titre">Voir la couverture des données</a></li>
					<li><a href="/couverture">Limites connues</a></li>
				</ul>
			</div>
			<ul class="etats-liste" aria-label="Les quatre absences">
				{#each ETATS as etat (etat.cle)}
					<li><BadgeEtat statut={{ cle: etat.cle, texte: LIBELLE_STATUT[etat.cle] }} /><span>{etat.phrase}</span></li>
				{/each}
			</ul>
		</section>

		<section class="acc-section" aria-labelledby="home-reperes-title">
			<h2 id="home-reperes-title">Repères nationaux</h2>
			<p class="section-p">Chiffres officiels repris tels que publiés, non calculés par DataCommune. Relevés le {DATE_RELEVE_REPERES}.</p>
			<dl class="cles cles-3 home-reperes">
				{#each REPERES_NATIONAUX as repere, i (repere.valeur)}
					<Tuile libelle={TEXTES.get(`repere:${i}`) ?? repere.libelle}>
						<span class="home-repere-valeur chiffre">{repere.valeur}</span>
						<SourceButton libelle={repere.libelle} onouvrir={(declencheur) => panneauxReperes[i]?.ouvrir(declencheur)} />
						<SourcePanel
							bind:this={panneauxReperes[i]}
							titre={repere.libelle}
							entrees={[
								{ terme: 'Portée', valeur: repere.portee },
								{ terme: 'Source', valeur: repere.source, url: repere.url }
							]}
							brut={[]}
						/>
					</Tuile>
				{/each}
			</dl>
		</section>
	</div>
</div>

<style>
	/* The solid head (charter of 06/11). No stacking context of its own: the suggestions of the field pass over
	   the card that overlaps it, and its ground stays under that card. */
	.hero {
		position: relative;
		background: var(--color-hero-fond);
		color: var(--color-hero-encre);
		text-align: center;
		--color-focus: var(--color-hero-encre);
	}

	/* The rings stay in their own frame: a clip on the head would cut the suggestions of the field. */
	.cadre-anneaux {
		position: absolute;
		inset: 0;
		overflow: hidden;
		pointer-events: none;
	}

	.anneaux {
		position: absolute;
		left: 50%;
		top: var(--centre-anneaux, 50%);
		width: 1900px;
		height: 1900px;
		transform: translate(-50%, -50%);
		animation: souffle 900ms cubic-bezier(0.16, 1, 0.3, 1) both;
	}

	.anneaux text {
		fill: var(--color-hero-encre-2);
		font-size: 14px;
		font-weight: 400;
		paint-order: stroke;
		stroke: var(--color-hero-anneau-2);
		stroke-width: 10px;
		stroke-linejoin: round;
	}

	.anneaux text.loin {
		stroke: var(--color-hero-anneau);
	}

	@keyframes souffle {
		from {
			transform: translate(-50%, -50%) scale(0.965);
		}
		to {
			transform: translate(-50%, -50%) scale(1);
		}
	}

	/* Positioned after the rings: what reads is painted over them. */
	.hero-int {
		position: relative;
		padding-block: var(--s7) var(--s8);
	}

	.hero h1 {
		margin: 0;
		font-size: 64px;
		line-height: 1.04;
		letter-spacing: -0.02em;
		text-wrap: balance;
		font-variant-numeric: lining-nums;
	}

	.acc-p {
		max-width: var(--mesure);
		margin: var(--s4) auto 0;
		color: var(--color-hero-encre-2);
		font-size: 20px;
		line-height: 1.45;
		text-wrap: balance;
	}

	.acc-champ {
		max-width: 720px;
		margin: var(--s6) auto 0;
		text-align: left;
	}

	.hero-suite {
		max-width: var(--mesure);
		margin: var(--s3) auto 0;
		color: var(--color-hero-encre-2);
	}

	.hero-suite a {
		display: inline-flex;
		align-items: center;
		min-height: 44px;
		color: var(--color-hero-lien);
		text-decoration-line: underline;
		text-decoration-thickness: 1px;
		text-underline-offset: 3px;
	}

	.hero-suite a:hover {
		color: var(--color-hero-encre);
		text-decoration-thickness: 2px;
	}

	.note-exemple {
		/* Under the head, the card it speaks of overlaps the last 72 px: these 48 keep the sentence clear of it. */
		max-width: var(--mesure);
		margin: var(--s-fin) auto var(--s7);
		color: var(--color-hero-encre-2);
		font-size: 14px;
		text-wrap: balance;
	}

	.note-exemple p {
		margin: 0;
	}

	/* The three examples in one cell, one shown: the cell is as tall as the tallest, nothing moves at a choice. */
	.pile {
		display: grid;
	}

	.pile > * {
		grid-area: 1 / 1;
		min-width: 0;
	}

	.pile > .cache,
	.pile-lien > .cache {
		visibility: hidden;
	}

	/* Flat notes on the rings, wide screens only: on a narrow one the example under the head says the same. */
	.faits {
		display: none;
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.fait {
		position: absolute;
		display: flex;
		align-items: center;
		gap: var(--s2);
		box-sizing: border-box;
		width: 222px;
		padding: var(--s2) var(--s3);
		border: 1px solid color-mix(in srgb, var(--color-hero-encre) 26%, transparent);
		border-radius: var(--radius-tuile);
		background: var(--color-hero-fond);
		color: var(--color-hero-encre);
		text-align: left;
		--c-encre: var(--color-hero-encre-2);
	}

	.fait b {
		display: block;
		font-family: var(--font-display);
		font-size: 20px;
		font-weight: 500;
		line-height: 1.1;
		font-variant-numeric: lining-nums tabular-nums;
	}

	.fait span {
		display: block;
		color: var(--color-hero-encre-2);
		font-size: 14px;
		line-height: 1.25;
	}

	.fait-1 {
		top: 48px;
		left: calc(50% - 640px);
	}

	.fait-2 {
		top: 188px;
		left: calc(50% - 720px);
	}

	.fait-3 {
		top: 328px;
		left: calc(50% - 640px);
	}

	.fait-4 {
		top: 48px;
		left: calc(50% + 418px);
	}

	.fait-5 {
		top: 188px;
		left: calc(50% + 498px);
	}

	.fait-6 {
		top: 328px;
		left: calc(50% + 418px);
	}

	@media (min-width: 1301px) {
		.faits {
			display: block;
		}
	}

	@media (max-width: 1560px) {
		.fait-1,
		.fait-3 {
			left: calc(50% - 618px);
		}

		.fait-2 {
			left: calc(50% - 650px);
		}

		.fait-4,
		.fait-6 {
			left: calc(50% + 396px);
		}

		.fait-5 {
			left: calc(50% + 428px);
		}
	}

	@media (max-width: 1400px) {
		.fait {
			width: 200px;
		}

		.fait-1,
		.fait-3 {
			left: 28px;
		}

		.fait-2 {
			left: 12px;
		}

		.fait-4,
		.fait-6 {
			right: 28px;
			left: auto;
		}

		.fait-5 {
			right: 12px;
			left: auto;
		}

		.acc-champ {
			max-width: 600px;
		}
	}

	@media (max-width: 1300px) {
		.note-exemple {
			display: none;
		}

		.hero-suite {
			margin-bottom: var(--s7);
		}

		.acc-champ {
			max-width: 720px;
		}
	}

	/* The example, overlapping the bottom of the head. */
	.exemple {
		position: relative;
		z-index: 2;
		margin-top: calc(-1 * var(--s8));
		padding: var(--s5);
		border: 1px solid var(--color-line);
		border-radius: var(--radius-panneau);
		background: var(--color-paper);
	}

	.choix-exemple {
		display: flex;
		flex-wrap: wrap;
		gap: var(--s2);
		margin-bottom: var(--s4);
	}

	.choix-bouton {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		box-sizing: border-box;
		min-height: 44px;
		padding: 0 var(--s4);
		border: 1px solid var(--color-line-strong);
		border-radius: var(--radius-control);
		background: var(--color-paper);
		color: var(--color-ink);
		font-family: inherit;
		font-weight: inherit;
		font-style: inherit;
		font-size: 16px;
		line-height: inherit;
		cursor: pointer;
	}

	.choix-bouton:hover {
		background: var(--color-soft);
	}

	/* A flex item drops the spaces at its ends: the separator keeps its own. */
	.choix-sep {
		white-space: pre;
	}

	/* The weight is on the name of the place in both states: a pressed button keeps its width. */
	.choix-ville {
		font-weight: 600;
	}

	/* The pressed choice as on « Données & méthode »: accent border on the soft ground. The accent ground
	   stays to the one primary action of the page, « Ouvrir cette fiche » (accent-unique.spec.ts). */
	.choix-bouton[aria-pressed='true'] {
		border-color: var(--color-accent);
		background: var(--color-soft);
		box-shadow: inset 0 0 0 1px var(--color-accent);
	}

	.ex-tete {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		justify-content: space-between;
		gap: var(--s3) var(--s5);
	}

	.ex-tete h2 {
		margin: 0;
		font-size: 30px;
		line-height: 1.15;
		letter-spacing: -0.01em;
		font-variant-numeric: lining-nums;
	}

	.ex-tete p {
		max-width: var(--mesure);
		margin: 0;
		color: var(--color-muted);
	}

	.etiquette {
		display: inline-flex;
		align-items: center;
		margin-bottom: var(--s1);
		padding: 0 var(--s2);
		border-radius: var(--radius-badge);
		background: var(--color-neutre-fond);
		color: var(--color-neutre);
		font-size: 14px;
		font-weight: 600;
	}

	.acc-bouton {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		gap: var(--s2);
		box-sizing: border-box;
		min-height: 44px;
		padding: 0 var(--s4);
		border: 1px solid var(--color-accent);
		border-radius: var(--radius-control);
		background: var(--color-accent);
		color: var(--color-paper);
		font-weight: 600;
		text-decoration: none;
		white-space: nowrap;
	}

	.acc-bouton:hover {
		border-color: var(--color-accent-hover);
		background: var(--color-accent-hover);
	}

	.acc-bouton .fleche {
		color: inherit;
	}

	.ex-chiffres {
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		gap: var(--s3);
		margin-top: var(--s4);
	}

	.mini {
		min-width: 0;
		padding: var(--s3) var(--s4);
		border-radius: var(--radius-tuile);
		background: var(--c-fond);
	}

	.mini-l {
		margin: 0;
		color: var(--c-encre);
		font-size: 14px;
		font-weight: 600;
	}

	.mini-v {
		display: flex;
		flex-wrap: wrap;
		gap: var(--s-fin) var(--s4);
		margin: var(--s1) 0 var(--s-demi);
	}

	.mini-v b {
		display: block;
		font-family: var(--font-display);
		font-size: 30px;
		font-weight: 500;
		line-height: 1.05;
		font-variant-numeric: lining-nums tabular-nums;
		white-space: nowrap;
	}

	.mini-v span {
		font-size: 14px;
		color: var(--color-muted);
	}

	.mini-dpe {
		display: flex;
		align-items: center;
		gap: var(--s2);
	}

	.mini-v .dpe-lettre {
		display: inline-flex;
		flex: none;
		align-items: center;
		justify-content: center;
		width: 40px;
		height: 40px;
		border-radius: var(--radius-control);
		font-family: var(--font-display);
		font-size: 26px;
		font-weight: 500;
		line-height: 1;
	}

	/* The two ends of the scale keep their white letter: a middle class takes the ink of the scale. */
	.mini-v .dpe-lettre:not(:global(.dpe-A), :global(.dpe-G)) {
		color: var(--color-dpe-ink);
	}

	.mini-v .dpe-lettre:global(.dpe-A) {
		color: var(--color-dpe-sur-a);
	}

	.mini-v .dpe-lettre:global(.dpe-G) {
		color: var(--color-dpe-sur-g);
	}

	.rangees {
		display: grid;
		gap: var(--s2);
		margin: var(--s3) 0 0;
		padding: 0;
		list-style: none;
	}

	/* A whole row is a link: the arrow in green says it, and the border at the hover. */
	.rangee {
		display: grid;
		grid-template-columns: 26px 170px minmax(0, 1fr) auto 22px;
		align-items: center;
		gap: var(--s1) var(--s3);
		padding: var(--s2) var(--s3);
		border: 1px solid transparent;
		border-radius: var(--radius-control);
		background: var(--c-fond);
		color: var(--color-ink);
		text-decoration: none;
	}

	.rangee:hover {
		border-color: var(--color-accent);
	}

	.rangee-ico {
		display: inline-flex;
	}

	.rangee-nom {
		color: var(--c-encre);
		font-family: var(--font-display);
		font-size: 20px;
		font-weight: 500;
		line-height: 1.15;
	}

	.rangee-res {
		min-width: 0;
		line-height: 1.35;
	}

	/* A count wraps in its row rather than push the page, with the text spacing a reader may set. */
	.rangee-compte {
		min-width: 0;
	}

	.rangee-compte :global(.badge) {
		line-height: 1.3;
		white-space: normal;
	}

	.fleche {
		flex: none;
		color: var(--color-accent);
		font-size: 18px;
		line-height: 1;
	}

	.acc-section {
		padding-top: var(--s7);
	}

	.acc-section h2,
	.acc-bande h2 {
		margin: 0;
		font-size: 32px;
		line-height: 1.12;
		letter-spacing: -0.01em;
		text-align: center;
		text-wrap: balance;
	}

	.section-p {
		max-width: var(--mesure);
		margin: var(--s2) auto 0;
		color: var(--color-muted);
		text-align: center;
		text-wrap: balance;
	}

	.usages,
	.entrees {
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.usages {
		gap: var(--s4);
		margin-top: var(--s5);
	}

	.entrees {
		gap: var(--s3);
		margin-top: var(--s3);
	}

	.carte-lien {
		display: flex;
		box-sizing: border-box;
		height: 100%;
		border: 1px solid var(--color-line);
		border-radius: var(--radius-control);
		background: var(--color-paper);
		color: var(--color-ink);
		text-decoration: none;
	}

	.carte-lien:hover {
		border-color: var(--color-accent);
	}

	.usages .carte-lien {
		flex-direction: column;
		gap: var(--s-demi);
		padding: var(--s4) var(--s5);
	}

	.usages h3 {
		margin: 0;
		color: var(--color-accent);
		font-family: var(--font-display);
		font-size: 24px;
		font-weight: 500;
		line-height: 1.15;
		text-decoration: underline;
		text-decoration-thickness: 1px;
		text-underline-offset: 4px;
	}

	.usage-texte {
		flex: 1;
		line-height: 1.45;
	}

	.usage-pied {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--s3);
		margin-top: var(--s2);
		color: var(--color-accent);
		font-weight: 600;
	}

	.entrees .carte-lien {
		align-items: center;
		justify-content: space-between;
		gap: var(--s3);
		padding: var(--s3) var(--s4);
	}

	.ex-t {
		display: block;
		color: var(--color-accent);
		font-weight: 600;
		text-decoration: underline;
		text-decoration-thickness: 1px;
		text-underline-offset: 3px;
	}

	.ex-n {
		display: block;
		color: var(--color-muted);
		font-size: 14px;
	}

	.pros {
		max-width: var(--mesure);
		margin: var(--s3) auto 0;
		color: var(--color-muted);
		text-align: center;
	}

	.acc-bande {
		margin-top: var(--s8);
		padding-block: var(--s7);
		background: var(--color-bande);
	}

	.portees {
		display: grid;
		grid-template-columns: repeat(5, minmax(0, 1fr));
		gap: var(--s4);
		margin: var(--s5) 0 0;
		padding: 0;
		list-style: none;
	}

	/* The circle grows with the scale inside a row as tall as the largest one, set on its bottom: the
	   titles of a row stay on one line. */
	.portee {
		display: grid;
		grid-template-rows: var(--rond-max) auto 1fr;
		justify-items: start;
		gap: var(--s2);
		min-width: 0;
	}

	.portee-rond {
		align-self: end;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		border-radius: 50%;
		background: var(--color-paper);
		box-shadow: inset 0 0 0 1px var(--color-line-strong);
	}

	.portee h3 {
		margin: 0;
		font-family: var(--font-display);
		font-size: 20px;
		font-weight: 500;
		line-height: 1.2;
	}

	.portee p {
		margin: 0;
		color: var(--color-muted);
		line-height: 1.4;
	}

	.acc-lien {
		max-width: var(--mesure);
		margin: var(--s4) auto 0;
		text-align: center;
	}

	/* Links out of a list row keep a 44 px target, as on the home page before the port (UI-SPEC § 8). */
	.acc-lien a,
	.home-method-links a {
		display: inline-flex;
		align-items: center;
		min-height: 44px;
	}

	/* A link inside a sentence: the padding of an inline box takes no room in the line and brings its
	   target to 44 px, where an inline-flex box would stretch the line that carries it. */
	.pros a {
		padding-block: var(--s3);
	}

	/* One link per example in one cell, as wide as the longest name: the sentence wraps the same way for
	   each. A grid item is a block, so its padding would stretch the line: the margin takes it back. */
	.pile-lien {
		display: inline-grid;
		grid-template-columns: minmax(0, max-content);
		max-width: 100%;
		margin-block: calc(-1 * var(--s3));
	}

	.pile-lien > a {
		grid-area: 1 / 1;
	}

	/* Links in a sentence or alone under a block: underlined, in accent (UI-SPEC § 3.0). Svelte scopes every
	   styled element with a class, so `a:not([class])` would match none: the links are named. */
	.acc-lien a,
	.pros a,
	.home-method-links a {
		color: var(--color-accent);
		text-decoration-line: underline;
		text-decoration-thickness: 1px;
		text-underline-offset: 3px;
	}

	.acc-lien a:hover,
	.pros a:hover,
	.home-method-links a:hover {
		color: var(--color-accent-hover);
		text-decoration-thickness: 2px;
	}

	.deux {
		display: grid;
		grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr);
		gap: var(--s6);
		align-items: start;
	}

	.deux h2 {
		text-align: left;
	}

	.deux p {
		max-width: var(--mesure);
		margin: var(--s3) 0 0;
	}

	.home-method-links {
		display: flex;
		flex-wrap: wrap;
		gap: 0 var(--s5);
		margin: var(--s2) 0 0;
		padding: 0;
		list-style: none;
	}

	.etats-liste {
		display: grid;
		gap: var(--s3);
		margin: 0;
		padding: var(--s4);
		border: 1px solid var(--color-line);
		border-radius: var(--radius-panneau);
		background: var(--color-paper);
		list-style: none;
	}

	.etats-liste li {
		display: grid;
		grid-template-columns: 172px minmax(0, 1fr);
		align-items: baseline;
		gap: var(--s3);
	}

	.etats-liste li :global(.badge) {
		justify-self: start;
	}

	.home-reperes :global(.cle) {
		position: relative;
		background: var(--color-paper);
	}

	/* In the corner of the tile, clear of its label. */
	.home-reperes :global(.cle dt) {
		padding-right: var(--s5);
	}

	.home-reperes :global(.source-button) {
		position: absolute;
		top: 0;
		right: 0;
		margin: 0;
	}

	.home-repere-valeur {
		font-family: var(--font-display);
		font-size: 30px;
		font-weight: 500;
		line-height: 1.2;
	}

	@media (max-width: 1080px) {
		.hero h1 {
			font-size: 48px;
		}

		.portees {
			grid-template-columns: repeat(3, minmax(0, 1fr));
		}
	}

	@media (max-width: 860px) {
		.ex-chiffres {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}

		.ex-chiffres .mini:last-child {
			grid-column: 1 / -1;
		}

		.rangee {
			grid-template-columns: 26px minmax(0, 1fr) 22px;
			grid-template-areas:
				'ico nom fl'
				'ico res fl'
				'ico cpt fl';
		}

		.rangee-ico {
			grid-area: ico;
			align-self: start;
		}

		.rangee-nom {
			grid-area: nom;
		}

		.rangee-res {
			grid-area: res;
		}

		.rangee-compte {
			grid-area: cpt;
		}

		.rangee .fleche {
			grid-area: fl;
		}

		.usages,
		.entrees,
		.deux {
			grid-template-columns: minmax(0, 1fr);
		}
	}

	@media (max-width: 720px) {
		.hero h1 {
			font-size: 38px;
		}

		.acc-p {
			font-size: 18px;
		}

		.anneaux {
			width: 1300px;
			height: 1300px;
		}

		.hero-int {
			padding-block: var(--s6) var(--s7);
		}

		.exemple {
			margin-top: calc(-1 * var(--s7));
			padding: var(--s4);
		}

		/* Three equal columns, the kind of place over its name. */
		.choix-exemple {
			display: grid;
			grid-template-columns: repeat(3, minmax(0, 1fr));
		}

		.choix-bouton {
			flex-direction: column;
			padding: var(--s1) var(--s-fin);
			font-size: 14px;
			line-height: 1.25;
			text-align: center;
			white-space: normal;
		}

		.choix-sep {
			display: none;
		}

		.ex-tete h2 {
			font-size: 26px;
		}

		.acc-bouton {
			width: 100%;
		}

		.mini {
			padding: var(--s2) var(--s3);
		}

		.mini-v b {
			font-size: 26px;
		}

		.portees {
			grid-template-columns: minmax(0, 1fr);
		}

		/* One per row: nothing to line up, no room left above the small circles. */
		.portee {
			grid-template-rows: auto;
		}

		.etats-liste li {
			grid-template-columns: minmax(0, 1fr);
			gap: var(--s-fin);
		}
	}

	/* On paper: the title and its sentence in ink, without the solid ground nor what stands on it. */
	@media print {
		.hero {
			background: transparent;
			color: var(--color-ink);
		}

		.acc-p {
			color: var(--color-muted);
		}

		.cadre-anneaux,
		.faits,
		.hero-suite,
		.note-exemple {
			display: none;
		}

		.exemple {
			margin-top: 0;
		}
	}
</style>
