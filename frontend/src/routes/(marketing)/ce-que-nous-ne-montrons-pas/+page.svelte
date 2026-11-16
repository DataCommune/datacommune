<script lang="ts">
	// « Ce que nous ne montrons pas, et pourquoi » (04.7-14, Origami R11): every deliberate limit with
	// its base, and the states a value can take. Linked from every fiche and from the footer.
	// Prerendered by the (marketing) layout: nothing is fetched.
	// 04.8-06 (UI-SPEC § 6.9, D-13): v4 form. Each limit is a definition line (`.def-l`, its lead in 600 on
	// the left, what is not shown and its base on the right); the states and their `#etats` anchor are kept.
	// Text unchanged word for word (`tests/e2e/fixtures/textes-information-reference.json`).
	import MetaPartage from '$lib/components/ds/MetaPartage.svelte';
	import { ETATS, LIMITES } from '$lib/data/ce-que-nous-ne-montrons-pas';
	import PageSimple from '$lib/components/ds/PageSimple.svelte';
	import Panneau from '$lib/components/ds/Panneau.svelte';
</script>

<MetaPartage titre="Ce que nous ne montrons pas, et pourquoi — DataCommune" description="Les informations que DataCommune ne montre pas (acheteurs, vendeurs, prix d'une vente isolée récente, occupants) et la base de chaque limite." chemin="/ce-que-nous-ne-montrons-pas" />

<PageSimple
	titre="Ce que nous ne montrons pas, et pourquoi"
	ariane="Ce que nous ne montrons pas"
	sousTitre="Les fiches réunissent des données publiques sur un lieu. Certaines informations n’y figurent jamais, par choix ou parce que la loi l’exclut. Voici chacune, avec sa base."
>
	<Panneau titre="Ce qui n’est pas montré">
		<ul class="def-l">
			{#each LIMITES as limite (limite.id)}
				<li class="ligne">
					<article class="def" id={limite.id} aria-labelledby={`${limite.id}-titre`}>
						<h3 id={`${limite.id}-titre`}>{limite.titre}</h3>
						<div class="def-corps">
							<p>{limite.quoi}</p>
							<dl class="base">
								<dt>Pourquoi</dt>
								<dd>{limite.pourquoi}</dd>
								<dt>Base</dt>
								<dd>{limite.base}</dd>
							</dl>
						</div>
					</article>
				</li>
			{/each}
		</ul>
	</Panneau>

	<Panneau titre="Quand une valeur manque" id="etats">
		<p>
			Une valeur absente n’est jamais affichée comme un zéro. Les fiches distinguent cinq cas, chacun
			avec son mot.
		</p>
		<dl class="etats">
			{#each ETATS as etat (etat.cle)}
				<div class="etat">
					<dt>{etat.libelle}</dt>
					<dd>{etat.definition}</dd>
				</div>
			{/each}
		</dl>
	</Panneau>

	<Panneau titre="Une erreur, une demande">
		<p>
			Une valeur vous semble fausse, ou une information vous concerne : écrivez à
			<a href="mailto:contact@datacommune.fr">contact@datacommune.fr</a>
			avec l’adresse de la page. Le détail des sources, de leurs périodes et de leurs licences est sur
			<a href="/datasets">Données &amp; méthode</a>, les limites
			connues des données sur
			<a href="/couverture">Couverture et limites</a>.
		</p>
	</Panneau>
</PageSimple>

<style>
	/* Definition lines (UI-SPEC § 6.9, `style.css:268-272` of the mockup): the lead on the left, at least
	   150 px; one column up to 720 px. A pale line between two lines, never after the last. */
	.def-l {
		margin: var(--s2) 0 0;
		padding: 0;
		list-style: none;
	}

	.ligne {
		padding: var(--s3) 0;
	}

	.ligne + .ligne {
		border-top: 1px solid var(--color-filet-pale);
	}

	.def {
		display: grid;
		grid-template-columns: minmax(150px, max-content) minmax(0, 1fr);
		gap: var(--s4);
		align-items: start;
	}

	.def h3 {
		max-width: 22ch;
		margin: 0;
		font-family: var(--font-sans);
		font-size: 16px;
		font-weight: 600;
		line-height: 1.5;
	}

	.def-corps > p {
		margin: 0;
	}

	.base,
	.etats {
		display: grid;
		grid-template-columns: max-content minmax(0, 1fr);
		gap: var(--s2) var(--s4);
		margin: var(--s3) 0 0;
	}

	.etat {
		display: contents;
	}

	.base dt,
	.etats dt {
		font-weight: 600;
	}

	.base dt {
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.6;
	}

	.base dd,
	.etats dd {
		max-width: var(--mesure);
		margin: 0;
	}

	@media (max-width: 720px) {
		.def,
		.base,
		.etats {
			grid-template-columns: minmax(0, 1fr);
			gap: var(--s1);
		}

		.base dd,
		.etats dd {
			margin-bottom: var(--s2);
		}
	}
</style>
