<script lang="ts">
  // Developer page (D-39). It replaces /docs, /reference and /playground: the interactive reference is
  // the one the API serves itself, so this prerendered page loads no third-party script (KI-P3: security
  // headers are not served on prerendered pages) and performs no /v1 call. No quota figure is written here.
  // 2026-11-11 (owner): the page opens on what answers without a key, the JSON export of a commune fiche,
  // as a command that works as written; the API with a key, which nobody can get on an instance without
  // accounts, comes last. Every example is read from the URL models (`ouverture.ts`), shared with /llms.txt.
  import MetaPartage from '$lib/components/ds/MetaPartage.svelte';
  import { ACCOUNTS_ENABLED, API_URL, OPENAPI_URL, SITE_URL } from '$lib/config';
  import { auth } from '$lib/stores/auth.svelte';
  import { EXTRAITS_EXPORT_COMMUNE } from '$lib/data/exemple-export-commune';
  import { CODE_SOURCE_URL, modeleUrl } from '$lib/data/ouverture';
  import PageSimple from '$lib/components/ds/PageSimple.svelte';
  import ListeLiens from '$lib/components/ds/ListeLiens.svelte';
  import Panneau from '$lib/components/ds/Panneau.svelte';

  const API_REFERENCE_URL = `${API_URL}/docs`;

  const adresse = modeleUrl('/adresse/{cle_ban}');
  const recherche = modeleUrl('/communes?q={texte}');
  // Every model read here has an example; the test of the page fails on a missing one.
  const exportCommune = modeleUrl('/commune/{code_insee}.json').exemple ?? '';
  const COMMANDE_EXPORT = `curl "${SITE_URL}${exportCommune}"`;
  const extrait = EXTRAITS_EXPORT_COMMUNE[/\/commune\/([^/]+)\.json$/.exec(exportCommune)?.[1] ?? ''];
  // The key and the commune are placeholders here (D-34): this command needs the reader's own key.
  const COMMANDE_API = `curl -H "X-API-Key: VOTRE_CLE" "${API_URL}/v1/commune?code_insee=CODE_INSEE"`;
  // Prerendered as anonymous (/register); switches to /settings once the session is known.
  const keyHref = $derived(auth.isAuthenticated ? '/settings' : '/register');

  let copiee = $state<string | null>(null);
  let retour: ReturnType<typeof setTimeout> | undefined;

  /** Copies *commande*; the button says so for two seconds. A refused copy leaves the text to select. */
  async function copier(commande: string) {
    try {
      await navigator.clipboard.writeText(commande);
    } catch {
      return;
    }
    copiee = commande;
    clearTimeout(retour);
    retour = setTimeout(() => (copiee = null), 2000);
  }
</script>

<MetaPartage titre="Développeurs" description="Les données d'une commune en JSON, sans compte ni clé ; contrat OpenAPI de l'API DataCommune." chemin="/developpeurs" />

<!-- The command wraps (I7, 2026-11-10): nothing scrolls, so the named wrapper needs no keyboard stop. -->
{#snippet commande(texte: string, role: 'region' | 'group', nom: string)}
  <div class="dev-exemple" {role} aria-label={nom}>
    <pre><code>{texte}</code></pre>
    <button type="button" class="bouton-secondaire" aria-live="polite" onclick={() => copier(texte)}>
      {copiee === texte ? 'Copié' : 'Copier'}
    </button>
  </div>
{/snippet}

<PageSimple
  titre="Développeurs"
  ariane="Développeurs"
  sousTitre="Les données d'une commune en JSON, sans compte ni clé."
>
  <Panneau titre="Une commune en JSON, sans clé">
    <p>Chaque fiche commune s'exporte en JSON, sans compte ni clé. Remplacez le code INSEE dans l'adresse.</p>
    {@render commande(COMMANDE_EXPORT, ACCOUNTS_ENABLED ? 'group' : 'region', "Commande curl de l'export d'une commune")}
    {#if extrait}
      <details class="depliant">
        <summary>Voir un extrait de la réponse</summary>
        <div class="dev-exemple" role="group" aria-label="Extrait de la réponse">
          <pre><code>{extrait}</code></pre>
        </div>
      </details>
    {/if}
    <dl class="dev-champs">
      <dt><code>etat</code></dt>
      <dd>
        <code>fiche</code>, <code>hors_couverture</code> ou <code>non_renseignee</code>. Une absence n'est jamais
        un zéro.
      </dd>
      <dt><code>sources</code></dt>
      <dd>Le producteur, la licence et la date de dernière importation de chaque jeu de données.</dd>
      <dt><code>donnees</code></dt>
      <dd>Les valeurs de la fiche, les mêmes que sur la page.</dd>
      <dt><code>lu_le</code></dt>
      <dd>La date de lecture. La réponse est gardée en cache une heure.</dd>
    </dl>
    <p>
      Le code INSEE a cinq caractères et n'est pas le code postal ; en Corse il commence par 2A ou 2B. Un code
      inconnu répond 404, une limite atteinte 429 avec un délai d'attente (<code>Retry-After</code>), une source
      en panne 503. Les erreurs sont au format <code>application/problem+json</code>.
    </p>
  </Panneau>

  <Panneau titre="Une adresse">
    <p>
      Il n'y a pas encore d'export JSON pour une adresse. Sa fiche se lit sur le site, à l'adresse
      <code>{adresse.modele}</code> : la clé est l'identifiant de l'adresse dans la Base adresse nationale.
    </p>
    <ListeLiens
      liens={[
        { href: adresse.exemple ?? '', titre: "Exemple de fiche d'adresse", note: adresse.exemple ?? '' },
        { href: recherche.exemple ?? '', titre: 'Chercher une adresse ou une commune', note: recherche.modele }
      ]}
    />
  </Panneau>

  <Panneau titre="Réutiliser les données">
    <p>
      Chaque valeur reste sous la licence de son producteur : la Licence Ouverte pour la plupart, l'ODbL pour les
      gares. Citez le producteur et la date de mise à jour ; mentionner DataCommune est bienvenu, sans être une
      condition.
    </p>
    <ListeLiens
      liens={[
        { href: '/datasets', titre: 'Sources, licences et fraîcheur de chaque jeu de données' },
        { href: '/llms.txt', titre: 'Résumé du site pour les programmes', note: '/llms.txt' },
        { href: '/cgu', titre: "Conditions d'utilisation" },
        { href: 'mailto:signalement@datacommune.fr', titre: 'Signaler un défaut', note: 'signalement@datacommune.fr' }
      ]}
    />
    {#if CODE_SOURCE_URL}
      <ListeLiens cible="externe" liens={[{ href: CODE_SOURCE_URL, titre: 'Code source' }]} />
    {/if}
  </Panneau>

  <Panneau titre="API avec clé">
    {#if ACCOUNTS_ENABLED}
      <p>
        Chaque appel porte l'en-tête <code>X-API-Key</code>. Créez et
        révoquez vos clés dans Réglages.
      </p>
      <a href={keyHref} class="dev-cle">Obtenir une clé API</a>
    {:else}
      <p>
        Ce site ne propose pas de comptes. L'API n'est pas encore ouverte aux clés personnelles : sans clé, elle
        répond 401.
      </p>
    {/if}
    <p><code class="dev-url">{API_URL}</code></p>
    <ListeLiens
      cible="externe"
      liens={[
        { href: OPENAPI_URL, titre: 'Spécification OpenAPI (JSON)' },
        { href: API_REFERENCE_URL, titre: "Référence interactive servie par l'API" }
      ]}
    />
    <p>
      Le contrat est en bêta publique : il peut encore changer.
      {#if ACCOUNTS_ENABLED}
        Chaque clé a un quota de requêtes ; une fois atteint, l'API répond 429. Votre quota et
        votre consommation s'affichent dans Réglages.
      {:else}
        Les requêtes sont limitées par adresse IP ; une fois la limite atteinte, l'API répond 429
        avec un délai d'attente (<code>Retry-After</code>).
      {/if}
    </p>
    {#if ACCOUNTS_ENABLED}
      {@render commande(COMMANDE_API, 'region', 'Exemple de requête curl')}
    {/if}
  </Panneau>
</PageSimple>

<style>
  code {
    padding: 0 var(--s1);
    background: var(--color-soft);
    font-size: inherit;
  }

  .dev-url {
    overflow-wrap: anywhere;
  }

  /* The plain action of the page: a link drawn as the accent button, 44 px high. */
  .dev-cle {
    display: inline-flex;
    align-items: center;
    min-height: 44px;
    padding: var(--s2) var(--s5);
    border-radius: var(--radius-control);
    background: var(--color-accent);
    color: var(--color-paper);
    font-weight: 600;
    text-decoration: none;
  }

  .dev-cle:hover {
    background: var(--color-accent-hover);
  }

  /* A command and its copy button; under the width of both, the button goes under the command. */
  .dev-exemple {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    max-width: 100%;
    border: 1px solid var(--color-line);
    border-radius: var(--radius-tuile);
    background: var(--color-soft);
  }

  .dev-exemple pre {
    flex: 1 1 16rem;
    min-width: 0;
    margin: 0;
    padding: var(--s4);
    font-size: 14px;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }

  .dev-exemple pre code {
    padding: 0;
    background: none;
  }

  .dev-exemple button {
    flex: none;
    margin: var(--s2) var(--s4) var(--s2) var(--s2);
  }

  details .dev-exemple {
    margin-bottom: var(--s2);
  }

  .dev-champs {
    display: grid;
    grid-template-columns: max-content minmax(0, 1fr);
    gap: var(--s2) var(--s4);
  }

  .dev-champs dt,
  .dev-champs dd {
    margin: 0;
  }

  @media (max-width: 720px) {
    .dev-champs {
      grid-template-columns: minmax(0, 1fr);
      gap: 0;
    }

    .dev-champs dd {
      margin-bottom: var(--s3);
    }
  }
</style>
