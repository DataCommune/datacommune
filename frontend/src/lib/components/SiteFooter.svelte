<script lang="ts">
	// Single site footer (D-50, UI-SPEC « En-tête et pied »), mounted by the public and the
	// connected layouts. Static on purpose: no counter and no fetch; sources and limits stay
	// available without exposing implementation vocabulary in the public note.
	// Under .public-site it takes the v4 form (UI-SPEC § 3.11, style.css:322-327 of the mockup) on the solid
	// ground of the charter of 06/11: padding 32 and 24 px, 14 px, the sentence at the reading measure. The sentence
	// is the mockup's one since every public surface is ported (04.8-08, D-34), without « son code sera
	// publié » (A-11).
	// On the address fiche showing its served map, the IGN licence of the map background is said here
	// and printed (UI-SPEC § 2.5, § 3.11): the only part of the footer on paper.
	import { page } from '$app/stores';
	import { CODE_SOURCE_URL } from '$lib/data/ouverture';
	import { CHEMIN_SIGNALER } from '$lib/signalement/contexte';
	import { SIGNALEMENT } from '$lib/signalement/reglage';

	const avecCarte = $derived(
		$page.route?.id === '/(marketing)/adresse/[id]' && Boolean(($page.data as { issue?: { rapport?: { carte?: unknown } } } | undefined)?.issue?.rapport?.carte)
	);

	const links = [
		{ href: '/datasets', label: 'Données & méthode' },
		// 04.8-06 (UI-SPEC § 3.11, D-14): the legend of the scales and states, from every page.
		{ href: '/datasets#lire', label: 'Lire une fiche' },
		{ href: '/couverture', label: 'Couverture et limites' },
		{ href: '/ce-que-nous-ne-montrons-pas', label: 'Ce que nous ne montrons pas' },
		{ href: '/developpeurs', label: 'Développeurs' },
		// The public source repository (D-137), right after the developer page (lot 6 of 05-07).
		{ href: CODE_SOURCE_URL, label: 'Code source' },
		{ href: '/mentions-legales', label: 'Mentions légales' },
		{ href: '/confidentialite', label: 'Confidentialité' },
		{ href: '/cgu', label: 'CGU' },
		// Socle (2026-11-09, I9): a way to report an error, from every page. The wording waits for Ianis.
		// The page of the report once it is switched on (framing of 2026-11-10); the mailto until then.
		{ href: SIGNALEMENT ? CHEMIN_SIGNALER : 'mailto:signalement@datacommune.fr', label: 'Signaler une erreur' }
	];
</script>

<!-- Site chrome, not printed, but for the licence of the map. -->
<footer class={avecCarte ? 'site-footer' : 'site-footer print:hidden'}>
	<div class="site-footer-inner">
		<nav aria-label="Pied de page" class="print:hidden">
			<ul class="site-footer-links">
				{#each links as link (link.href)}
					<li><a href={link.href}>{link.label}</a></li>
				{/each}
			</ul>
		</nav>
		<p class="site-footer-note print:hidden">
			<strong>DataCommune</strong> rassemble des données publiques ouvertes. Chaque valeur cite sa source, sa date et
			l’échelle à laquelle elle vaut.
		</p>
		{#if avecCarte}<p class="site-footer-ign">Fonds de carte&nbsp;: Plan IGN, ©&nbsp;IGN, Licence Ouverte 2.0.</p>{/if}
	</div>
</footer>

<style>
	/* Connected form (the default): the values of 03-03, written with the tokens of :root (D-27). */
	.site-footer {
		margin-top: var(--s7);
		border-top: 1px solid var(--color-line);
		background: var(--color-bg);
		color: var(--color-muted);
	}

	.site-footer-inner {
		max-width: 1280px;
		margin: 0 auto;
		padding: var(--s5) var(--s6) var(--s6);
		text-align: center;
	}

	.site-footer-links {
		display: flex;
		flex-wrap: wrap;
		justify-content: center;
		gap: 0 var(--s4);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.site-footer-links a {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		min-height: 44px;
		min-width: 44px;
		color: var(--color-ink);
		font-size: 14px;
		line-height: 1.4;
		text-decoration: underline;
		text-underline-offset: 4px;
	}

	.site-footer-note,
	.site-footer-ign {
		margin: var(--s2) 0 0;
		font-size: 14px;
		line-height: 1.4;
		overflow-wrap: anywhere;
	}

	@media (max-width: 999px) {
		.site-footer-inner {
			padding: var(--s5) var(--s5) var(--s6);
		}
	}

	@media (max-width: 720px) {
		.site-footer-inner {
			padding: var(--s4) var(--s4) var(--s5);
		}
	}

	/* Public form: a grid of the links and the sentence, gaps of 12 px; the links wrap with gaps of
	   8 and 24 px and keep their 44 px targets. */
	:global(.public-site) .site-footer {
		border-top: 0;
		background: var(--color-pied);
		color: var(--color-pied-encre);
		--color-focus: var(--color-pied-encre);
	}

	:global(.public-site) .site-footer-inner {
		display: grid;
		gap: var(--s3);
		max-width: var(--largeur-page);
		padding: var(--s6) var(--s5);
		font-size: 14px;
		text-align: left;
	}

	:global(.public-site) .site-footer-links {
		justify-content: flex-start;
		gap: var(--s2) var(--s5);
	}

	:global(.public-site) .site-footer-links a {
		color: var(--color-pied-lien);
	}

	:global(.public-site) .site-footer-links a:hover {
		color: var(--color-pied-encre);
	}

	:global(.public-site) .site-footer-note {
		max-width: var(--mesure);
		margin: 0;
	}

	/* The edge of `.gabarit`: this block outweighs it, so it writes the same two values. */
	@media (max-width: 720px) {
		:global(.public-site) .site-footer-inner {
			padding-inline: var(--s4);
		}
	}

	/* Only the licence of the map is printed, on the paper of the page. */
	@media print {
		:global(.public-site) .site-footer {
			background: transparent;
			color: var(--color-ink);
		}
	}
</style>
