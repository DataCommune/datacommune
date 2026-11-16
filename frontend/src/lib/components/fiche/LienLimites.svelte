<script lang="ts">
	// Discreet link from every fiche header to « Ce que nous ne montrons pas » (04.7-14, Origami R11,
	// A11): what the fiche shows and what it never shows, with the base of each limit.
	import { CHEMIN_CE_QUE_NOUS_NE_MONTRONS_PAS, LIEN_CE_QUE_NOUS_NE_MONTRONS_PAS } from '$lib/data/ce-que-nous-ne-montrons-pas';
</script>

<p class="lien-limites print:hidden">
	<a href={CHEMIN_CE_QUE_NOUS_NE_MONTRONS_PAS}>{LIEN_CE_QUE_NOUS_NE_MONTRONS_PAS}</a>
</p>

<style>
	.lien-limites {
		margin: 0;
		font-size: 14px;
		line-height: 1.4;
	}

	.lien-limites a {
		display: inline-flex;
		align-items: center;
		min-height: 44px;
		color: var(--color-accent);
		text-decoration: underline;
		text-underline-offset: 4px;
	}
</style>
