<script lang="ts">
	// The counts of the whole fiche, at the head of « Risques » (4.9, reading of 06/11; CADRAGE § 6): the
	// sentence that used to open « En bref », read from the served lists (`phraseEnBref`), and the served
	// vigilances of the scale wider than the address. They left the arrival, where a commune fact said nothing
	// about the address: nothing is lost and nothing is counted here.
	import { phraseEnBref, type ResumeFiche } from '$lib/data/adresse-rapport';
	import { perimetreSecteur } from '$lib/data/echelles';
	import { typographieFr } from '$lib/utils/typographie';

	interface Props {
		resume: ResumeFiche;
		/** Prose of the page with its acronyms spelled out: `bref:phrase:<i>`, `bref:bref-l-secteur:<id>`. */
		textes?: ReadonlyMap<string, string>;
	}
	let { resume, textes }: Props = $props();

	const phrase = $derived(phraseEnBref(resume));
	const perimetre = $derived(perimetreSecteur(resume.pointsSecteur));
</script>

<div class="bilan">
	<p class="bref-p">Sur toute la fiche&nbsp;: {#each phrase as segment, i (i)}{@const texte = textes?.get(`bref:phrase:${i}`) ?? segment.texte}{#if segment.fort}<strong>{texte}</strong>{:else}{texte}{/if}{/each}</p>
	<h4>Vigilances {perimetre}</h4>
	{#if resume.pointsSecteur.length}
		<ul class="bref-l bref-l-secteur">
			{#each resume.pointsSecteur as theme (theme.id)}
				<li>
					<a href={`#critere-${theme.id}`}
						><span class="bref-t">{textes?.get(`bref:bref-l-secteur:${theme.id}`) ?? typographieFr(theme.titre)}</span
						>{#if theme.accroche}{' '}<span class="bref-v">{theme.accroche}</span>{/if}<span class="fleche" aria-hidden="true">↓</span></a
					>
				</li>
			{/each}
		</ul>
	{:else}
		<p class="bref-vide">{resume.lusSecteur ? `Rien à signaler ${perimetre}.` : 'Aucun critère lu à cette échelle.'}</p>
	{/if}
</div>

<style>
	.bilan {
		padding: var(--s3) 0;
		border-bottom: 1px solid var(--color-filet-pale);
	}

	.bref-p {
		max-width: var(--mesure);
		margin: 0;
		font-size: 16px;
		line-height: 1.5;
	}

	.bref-p strong {
		font-weight: 600;
	}

	h4 {
		margin: var(--s3) 0 var(--s-demi);
		color: var(--color-muted);
		font-family: var(--font-sans);
		font-size: 14px;
		font-weight: 600;
		line-height: 1.4;
	}

	.bref-l {
		display: flex;
		flex-wrap: wrap;
		gap: var(--s-fin) var(--s4);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.bref-l a {
		color: var(--color-ink);
		text-decoration-line: underline;
		text-decoration-color: var(--color-line-strong);
		text-decoration-thickness: 1px;
		text-underline-offset: 3px;
	}

	.bref-l a:hover {
		text-decoration-color: currentColor;
	}

	.bref-v {
		color: var(--color-muted);
	}

	.fleche {
		margin-left: var(--s-demi);
		color: var(--color-accent);
	}

	.bref-vide {
		max-width: var(--mesure);
		margin: 0;
		color: var(--color-muted);
		font-size: 14px;
	}

	@media (max-width: 720px) {
		/* 44 px targets (§ 2.3), one name per line. */
		.bref-l {
			flex-direction: column;
		}

		.bref-l a {
			display: inline-flex;
			flex-wrap: wrap;
			align-items: center;
			gap: 0 var(--s2);
			min-height: 44px;
		}

		.fleche {
			margin-left: 0;
		}
	}
</style>
