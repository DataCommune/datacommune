<script lang="ts">
	// Print-only header of BOTH public fiches (UI-04, D-10/D-11/D-12/D-14, plan 50-07).
	//
	// It exists because the printed document used to say nothing about itself: no date, no
	// qualification of what it is, no pointer to the official sources it does NOT replace.
	//
	// WHAT IT DOES NOT CONTAIN — the title. The `<h1>` of the page now prints (the `@media print`
	// rule of app.css stopped hiding every `header` element, task 1 of this plan), so reproducing
	// the address / commune name here would put TWO titles on the PDF.
	//
	// NOTHING IS WRITTEN BY THIS COMPONENT except the three locked strings below: the date comes
	// from the loader (`consulteLe`, D-12), the mention and the renvoi are verbatim copy of the
	// 50-UI-SPEC Copywriting Contract. Reading order is locked by the same contract:
	// date → mention → renvoi.
	//
	// No `<a href>` on the two URLs, on purpose: this block is never on screen, and a print-out
	// cannot follow a link — the URL in clear text IS the usable form (D-11).

	interface Props {
		/**
		 * « JJ/MM/AAAA » computed at load time by `dateConsultationFr()`. `null` on the loader
		 * branches that render no fiche — the date line is then simply absent, never « null ».
		 */
		consulteLe: string | null;
	}

	let { consulteLe }: Props = $props();

	// Canonical URLs of the two official services, byte-identical to `SOURCE_INFO.errial.url` and
	// `SOURCE_INFO.georisques.url` (src/lib/api/admin.ts). Copied rather than imported: importing
	// `$lib/api/admin` would pull the admin API client into the PUBLIC fiche bundle for two string
	// constants. The equality with the catalogue is pinned by `EnTetePrint.test.ts`, so a drift is
	// a red test, not a silent divergence.
	const URL_ERRIAL = 'https://errial.georisques.gouv.fr';
	const URL_GEORISQUES = 'https://georisques.gouv.fr';
</script>

<!-- `hidden print:block` — invisible on screen, restored under @media print (Tailwind v4 native
     variants, the same grammar as the `print:hidden` already used on this fiche). No dedicated
     `/print` route, no page fork: the printed page IS the verified page (D-10).
     `.bloc-impression` is the hook of the `break-inside: avoid` rule of app.css (D-14). -->
<div class="bloc-impression hidden print:block">
	{#if consulteLe}
		<p class="ligne date">Consulté le {consulteLe}</p>
	{/if}
	<p class="ligne mention">Synthèse informative — ne remplace pas les diagnostics officiels</p>
	<p class="ligne renvoi">
		Diagnostics et risques officiels&nbsp;: ERRIAL {URL_ERRIAL} · Géorisques {URL_GEORISQUES}
	</p>
</div>

<style>
	/* v4 form (UI-SPEC v4 § 2.5, § 2.3): spacing tokens only, the 14 px metadata size for the three
	   lines, the reading measure on the paragraphs. Nothing is declared here or added to app.css. */
	.bloc-impression {
		margin-bottom: var(--s4);
		padding-bottom: var(--s2);
		border-bottom: 1px solid var(--color-line);
	}
	.ligne {
		max-width: var(--mesure);
		margin: 0 0 var(--s1);
		font-size: 14px;
		font-weight: 400;
		line-height: 1.4;
		color: var(--color-muted);
	}
	.ligne.date {
		color: var(--color-ink);
	}
	.ligne.renvoi {
		margin-bottom: 0;
	}
</style>
