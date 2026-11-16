<script lang="ts" module>
	export interface LienDeListe {
		href: string;
		titre: string;
		/** Secondary line, on the right (full width, on the left, under 720 px). */
		note?: string;
	}
</script>

<script lang="ts">
	// List of links out of running text (UI-SPEC v4 § 3.0, § 3.10): the whole row is the link, not
	// underlined, its text in ink; the arrow is the only accent, a character in a span hidden from
	// assistive technologies, last child of the link (the `content: "→" / ""` of the mockup is not ported:
	// its support outside Chromium is not established). → another page of the site, ↓ an anchor of the
	// page, ↗ an external site, which opens with `rel="noopener noreferrer"` (T-04.8-05).
	type Cible = 'page' | 'ancre' | 'externe';

	interface Props {
		liens: readonly LienDeListe[];
		cible?: Cible;
	}

	let { liens, cible = 'page' }: Props = $props();

	const FLECHES: Record<Cible, string> = { page: '→', ancre: '↓', externe: '↗' };
</script>

<ul class="liste-liens" class:liste-liens-externe={cible === 'externe'}>
	{#each liens as lien, index (index)}
		<li>
			<a href={lien.href} rel={cible === 'externe' ? 'noopener noreferrer' : undefined}
				><span class="lien-t">{lien.titre}</span>{#if lien.note}<span class="lien-n">{lien.note}</span>{/if}<span
					class="fleche"
					aria-hidden="true">{FLECHES[cible]}</span
				></a
			>
		</li>
	{/each}
</ul>

<style>
	.liste-liens {
		margin: var(--s3) 0 0;
		padding: 0;
		list-style: none;
	}

	.liste-liens li + li {
		border-top: 1px solid var(--color-filet-pale);
	}

	.liste-liens a {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: var(--s-fin) var(--s3);
		margin: 0 calc(-1 * var(--s2));
		padding: var(--s3) var(--s2);
		border-radius: var(--radius-ligne);
		color: var(--color-ink);
		text-decoration: none;
	}

	.liste-liens a:hover {
		background: var(--color-soft);
	}

	.lien-t {
		flex: 1 1 14rem;
		font-family: var(--font-sans);
		min-width: 0;
		font-weight: 600;
		overflow-wrap: anywhere;
	}

	.lien-n {
		flex: 0 1 auto;
		min-width: 0;
		color: var(--color-muted);
		font-size: 14px;
		text-align: right;
		overflow-wrap: anywhere;
	}

	/* Last child of the link already: no `order`, the visual order is the DOM order (UI-01 invariant). */
	.fleche {
		flex: none;
		color: var(--color-accent);
	}

	@media (max-width: 720px) {
		.lien-n {
			flex-basis: 100%;
			text-align: left;
		}
	}

	/* On paper, an external link prints its address after it (UI-SPEC v4 § 2.5). */
	@media print {
		.liste-liens-externe a::after {
			content: ' (' attr(href) ')';
			flex-basis: 100%;
			color: var(--color-muted);
			font-size: 14px;
			overflow-wrap: anywhere;
		}
	}
</style>
