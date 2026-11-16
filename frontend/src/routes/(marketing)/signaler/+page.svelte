<script lang="ts">
	// Page of the error report (framing of 2026-11-10): one message, an optional e-mail address, and
	// the context joined shown before anything is sent. A plain form: it works without JavaScript;
	// with it, the page is not reloaded and the focus goes where the answer is (the field refused, the
	// failure, or the confirmation). Every wording comes from `$lib/signalement/libelles` (to validate).
	// Nothing typed or joined is ever rendered as HTML: text interpolation only.
	import { enhance } from '$app/forms';
	import PageSimple from '$lib/components/ds/PageSimple.svelte';
	import Panneau from '$lib/components/ds/Panneau.svelte';
	import { BORNES, type ContexteSignalement, type ErreursSaisie } from '$lib/signalement/contexte';
	import { ADRESSE_SIGNALEMENT, LIBELLES } from '$lib/signalement/libelles';

	interface Retour {
		recu?: { reference: string | null; retour: string | null };
		erreurs?: ErreursSaisie;
		panne?: 'debit' | 'envoi';
		valeurs?: { commentaire: string; email: string };
		contexte?: ContexteSignalement;
	}

	interface Props {
		data: { contexte: ContexteSignalement; navigateur: string | null };
		form?: Retour | null;
	}

	let { data, form = null }: Props = $props();

	const uid = $props.id();
	// After a refusal the form gives back the context it was sent with.
	const contexte = $derived(form?.contexte ?? data.contexte);
	const erreurs = $derived(form?.erreurs ?? {});
	// Known in the browser only: left empty without JavaScript.
	let largeur = $state<number | null>(null);
	let enCours = $state(false);
	let champCommentaire = $state<HTMLTextAreaElement>();
	let champEmail = $state<HTMLInputElement>();
	let panne = $state<HTMLParagraphElement>();
	let confirmation = $state<HTMLParagraphElement>();

	$effect(() => {
		largeur = Math.min(window.innerWidth, BORNES.largeur);
	});

	// The focus goes where the answer is, once it is rendered: after an answer received without a
	// reload, and on the page a plain post reloaded (the form was sent before hydration).
	$effect(() => {
		if (form?.recu) confirmation?.focus();
		else if (form?.erreurs?.commentaire) champCommentaire?.focus();
		else if (form?.erreurs?.email) champEmail?.focus();
		else if (form?.panne) panne?.focus();
	});

	// Given back after a refusal only. Without an answer the fields carry no value of ours: a value
	// set at hydration would erase what was typed while the page was loading.
	const emailRendu = $derived(form?.valeurs ? { value: form.valeurs.email } : null);
	const attributsCommentaire = $derived({
		id: `${uid}-commentaire`,
		name: 'commentaire',
		rows: 6,
		required: true,
		maxlength: BORNES.commentaireMax,
		'aria-describedby': `${uid}-commentaire-aide${erreurs.commentaire ? ` ${uid}-commentaire-erreur` : ''}`,
		'aria-invalid': erreurs.commentaire ? ('true' as const) : undefined
	});
</script>

<svelte:head>
	<title>{LIBELLES.onglet}</title>
	<meta name="robots" content="noindex, nofollow" />
</svelte:head>

{#if form?.recu}
	<PageSimple titre={LIBELLES.recu} ariane={LIBELLES.titre}>
		<p class="signaler-recu" tabindex="-1" bind:this={confirmation}>
			{LIBELLES.merci}
			{#if form.recu.reference}{LIBELLES.reference}<strong>{form.recu.reference}</strong>.{/if}
		</p>
		{#if form.recu.retour}
			<p><a class="bouton-secondaire" href={form.recu.retour}>{LIBELLES.retour}</a></p>
		{/if}
	</PageSimple>
{:else}
	<PageSimple titre={LIBELLES.titre} ariane={LIBELLES.titre} sousTitre={LIBELLES.ouverture}>
		<form
			method="POST"
			class="signaler"
			novalidate
			aria-busy={enCours}
			use:enhance={() => {
				enCours = true;
				return async ({ update }) => {
					// What was typed stays in the fields after a refusal.
					await update({ reset: false });
					enCours = false;
				};
			}}
		>
			{#if form?.panne}
				<p class="signaler-panne" role="alert" tabindex="-1" bind:this={panne}>
					{LIBELLES.pannes[form.panne]}<a href="mailto:{ADRESSE_SIGNALEMENT}">{ADRESSE_SIGNALEMENT}</a>.
				</p>
			{/if}

			<div class="signaler-champ">
				<label for="{uid}-commentaire">{LIBELLES.commentaire}</label>
				<!-- Two forms of the same field: its content is written only when a refusal gives the message
				     back (the server renders it, so it is there without JavaScript); otherwise the field has
				     no content of ours, and hydration leaves what was typed while the page was loading. -->
				{#if form?.valeurs}
					<textarea {...attributsCommentaire} bind:this={champCommentaire}>{form.valeurs.commentaire}</textarea>
				{:else}
					<textarea {...attributsCommentaire} bind:this={champCommentaire}></textarea>
				{/if}
				{#if erreurs.commentaire}
					<p class="signaler-erreur" id="{uid}-commentaire-erreur">{LIBELLES.erreurs[erreurs.commentaire]}</p>
				{/if}
				<p class="signaler-aide" id="{uid}-commentaire-aide">{LIBELLES.aideCommentaire}</p>
			</div>

			<div class="signaler-champ">
				<label for="{uid}-email">{LIBELLES.email}</label>
				<input
					id="{uid}-email"
					name="email"
					type="email"
					autocomplete="email"
					maxlength={BORNES.email}
					{...emailRendu}
					aria-describedby="{uid}-email-aide{erreurs.email ? ` ${uid}-email-erreur` : ''}"
					aria-invalid={erreurs.email ? 'true' : undefined}
					bind:this={champEmail}
				/>
				{#if erreurs.email}
					<p class="signaler-erreur" id="{uid}-email-erreur">{LIBELLES.erreurs[erreurs.email]}</p>
				{/if}
				<p class="signaler-aide" id="{uid}-email-aide">{LIBELLES.aideEmail}</p>
			</div>

			<!-- Honeypot: out of sight, out of the tab order and of the accessibility tree. -->
			<div class="signaler-piege" aria-hidden="true">
				<label for="{uid}-site-web">Site web</label>
				<input id="{uid}-site-web" name="site_web" type="text" tabindex="-1" autocomplete="off" />
			</div>

			<input type="hidden" name="page" value={contexte.page ?? ''} />
			<input type="hidden" name="titre" value={contexte.titre ?? ''} />
			<input type="hidden" name="lignes" value={contexte.lignes ?? ''} />
			<input type="hidden" name="source" value={contexte.source ?? ''} />
			<input type="hidden" name="version" value={contexte.version} />
			<input type="hidden" name="largeur" value={largeur ?? ''} />

			<Panneau titre={LIBELLES.joint}>
				<dl class="signaler-joint">
					{#if contexte.page}
						<div><dt>{LIBELLES.termes.page}</dt><dd>{contexte.page}</dd></div>
					{/if}
					{#if contexte.titre || contexte.lignes}
						<div>
							<dt>{LIBELLES.termes.valeur}</dt>
							<dd>
								{#if contexte.titre}{contexte.titre}{/if}
								{#if contexte.lignes}<code>{contexte.lignes}</code>{/if}
							</dd>
						</div>
					{/if}
					{#if contexte.source}
						<div><dt>{LIBELLES.termes.source}</dt><dd>{contexte.source}</dd></div>
					{/if}
					<div><dt>{LIBELLES.termes.version}</dt><dd>{contexte.version}</dd></div>
					{#if largeur !== null}
						<div><dt>{LIBELLES.termes.largeur}</dt><dd>{largeur} px</dd></div>
					{/if}
					{#if data.navigateur}
						<div><dt>{LIBELLES.termes.navigateur}</dt><dd>{data.navigateur}</dd></div>
					{/if}
				</dl>
			</Panneau>

			<p class="signaler-action">
				<button type="submit" class="bouton-primaire" disabled={enCours}>{LIBELLES.envoyer}</button>
			</p>
			<p class="signaler-aide">{LIBELLES.conservation}<a href="/confidentialite">{LIBELLES.lienConfidentialite}</a>.</p>
			<p class="signaler-aide">{LIBELLES.autreVoie}<a href="mailto:{ADRESSE_SIGNALEMENT}">{ADRESSE_SIGNALEMENT}</a></p>
		</form>
	</PageSimple>
{/if}

<style>
	.signaler {
		display: grid;
		gap: var(--s5);
		max-width: var(--mesure);
		margin-top: var(--s5);
	}

	.signaler-champ {
		display: grid;
		gap: var(--s1);
	}

	.signaler-champ label {
		font-size: 16px;
		font-weight: 600;
	}

	.signaler-champ :is(textarea, input) {
		box-sizing: border-box;
		width: 100%;
		min-height: 44px;
		padding: var(--s2) var(--s3);
		border: 1px solid var(--color-line-strong);
		background: var(--color-paper);
		color: var(--color-ink);
		font-family: inherit;
		font-size: 16px;
		line-height: 1.5;
	}

	.signaler-champ textarea {
		resize: vertical;
	}

	.signaler-champ :is(textarea, input)[aria-invalid='true'] {
		border-color: var(--color-danger);
	}

	.signaler :global(p) {
		margin: 0;
	}

	.signaler-aide {
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
	}

	.signaler-erreur,
	.signaler-panne {
		color: var(--color-danger);
		font-size: 16px;
		font-weight: 600;
	}

	/* Off the page rather than `display: none`, which some robots skip. */
	.signaler-piege {
		position: absolute;
		left: -9999px;
		width: 1px;
		height: 1px;
		overflow: hidden;
	}

	.signaler-joint {
		display: grid;
		gap: var(--s2);
		margin: 0;
		font-size: 14px;
	}

	.signaler-joint > div {
		display: grid;
		grid-template-columns: minmax(0, 11rem) minmax(0, 1fr);
		gap: var(--s3);
	}

	.signaler-joint dt {
		color: var(--color-muted);
	}

	.signaler-joint dd {
		margin: 0;
		overflow-wrap: anywhere;
	}

	.signaler-joint code {
		display: block;
		font-family: var(--font-sans);
		font-size: 14px;
	}

	.signaler-recu {
		margin-top: var(--s5);
		font-size: 18px;
	}

	@media (max-width: 720px) {
		.signaler-joint > div {
			grid-template-columns: minmax(0, 1fr);
			gap: 0;
		}
	}
</style>
