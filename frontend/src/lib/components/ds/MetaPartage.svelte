<script lang="ts">
	// Sharing metadata of a public page (socle of 2026-11-09, T1): the title, the description, the canonical
	// and the card a shared link shows, as the reference pages write them by hand (`commune/[code]`). A page
	// that mounts this component writes none of these tags itself: `<meta>` is not deduplicated in the head.
	//   - `titre` is the whole `<title>`, as the page words it;
	//   - `chemin` is the address of the page on the site (`/datasets`): it gives `og:url` and, unless
	//     `canonique` is false, the canonical. A page kept out of the index has no canonical;
	//   - `robots` is written only when given (`noindex, follow`).
	//   - `image` is the page's own preview (`$lib/seo/og-carte`) with its alternative text; the generic
	//     image of the site without it.
	// JSON-LD stays with the page: it is data of the page, not of the card.
	import { OG_IMAGE_URL, SITE_URL } from '$lib/config';
	import { OG_HAUTEUR, OG_LARGEUR } from '$lib/seo/og-carte';

	interface Props {
		titre: string;
		description: string;
		/** Path of the page, from the root of the site. */
		chemin?: string;
		/** False on a page kept out of the index: no canonical, the card keeps its address. */
		canonique?: boolean;
		/** Content of `meta robots`; none without it. */
		robots?: string;
		/** Preview of the page; the generic image of the site when absent. */
		image?: { url: string; alt: string } | null;
	}

	let { titre, description, chemin, canonique = true, robots, image = null }: Props = $props();

	const url = $derived(chemin === undefined ? null : `${SITE_URL}${chemin}`);
</script>

<svelte:head>
	<title>{titre}</title>
	<meta name="description" content={description} />
	{#if robots}<meta name="robots" content={robots} />{/if}
	{#if url && canonique}<link rel="canonical" href={url} />{/if}
	<meta property="og:type" content="website" />
	<meta property="og:site_name" content="DataCommune" />
	<meta property="og:title" content={titre} />
	<meta property="og:description" content={description} />
	{#if url}<meta property="og:url" content={url} />{/if}
	<meta property="og:image" content={image?.url ?? OG_IMAGE_URL} />
	<meta property="og:image:width" content={String(OG_LARGEUR)} />
	<meta property="og:image:height" content={String(OG_HAUTEUR)} />
	{#if image}<meta property="og:image:alt" content={image.alt} />{/if}
	<meta property="og:locale" content="fr_FR" />
	<meta name="twitter:card" content="summary_large_image" />
	<meta name="twitter:title" content={titre} />
	<meta name="twitter:description" content={description} />
	<meta name="twitter:image" content={image?.url ?? OG_IMAGE_URL} />
</svelte:head>
