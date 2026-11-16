<script lang="ts">
  // Legal notice (audit C16, 04.7-14): publisher and director of publication, hosting and routing,
  // personal data pointing to the privacy policy (no duration restated here), the main sources and
  // their licence from the shared list of `ouverture.ts`, the attribution asked, and the limits page.
  // 04.8-06 (UI-SPEC § 6.9, D-13): v4 form, one simple panel per section; the text is unchanged word
  // for word (compared to `tests/e2e/fixtures/textes-information-reference.json`). The « key : value »
  // pairs of the text go in a `dl.tab`; the colon stays in the text, hidden from the eye only.
  import MetaPartage from '$lib/components/ds/MetaPartage.svelte';
  import { CHEMIN_CE_QUE_NOUS_NE_MONTRONS_PAS } from '$lib/data/ce-que-nous-ne-montrons-pas';
  import { ATTRIBUTION, CODE_SOURCE_URL, SOURCES_PRINCIPALES } from '$lib/data/ouverture';
  import PageSimple from '$lib/components/ds/PageSimple.svelte';
  import ListeLiens from '$lib/components/ds/ListeLiens.svelte';
  import Panneau from '$lib/components/ds/Panneau.svelte';
</script>

<MetaPartage titre="Mentions légales — DataCommune" description="Mentions légales du site DataCommune." chemin="/mentions-legales" />

<PageSimple titre="Mentions légales" ariane="Mentions légales" sousTitre="Dernière mise à jour : 31 octobre 2026">
  <Panneau titre="Éditeur du site">
    <p>
      DataCommune est un projet personnel édité à titre non professionnel par un particulier :
      Ianis P.
    </p>
    <dl class="tab">
      <div class="tab-l">
        <dt>Email<span class="sr-only"> : </span></dt>
        <dd><a href="mailto:contact@datacommune.fr">contact@datacommune.fr</a></dd>
      </div>
      <div class="tab-l">
        <dt>Directeur de la publication<span class="sr-only"> : </span></dt>
        <dd>Ianis P., éditeur du site.</dd>
      </div>
    </dl>
  </Panneau>

  <Panneau titre="Hébergement">
    <p>
      Le site et l'API sont auto-hébergés par l'éditeur, sur son infrastructure personnelle,
      en France. Il n'y a pas d'hébergeur tiers.
    </p>
    <p>
      Le trafic vers le site et l'API est acheminé par Cloudflare, Inc., 101 Townsend St,
      San Francisco, CA 94107, États-Unis (réseau et tunnel d'accès).
    </p>
  </Panneau>

  <Panneau titre="Données personnelles">
    <p>
      Le site n'a pas de comptes et ne demande aucune information personnelle. Aucun cookie
      de suivi n'est utilisé : le seul cookie, technique, porte la limite de consultation des
      fiches d'adresse. La mesure d'audience, faite chez l'éditeur, n'en dépose aucun. L'adresse
      IP du visiteur est traitée pour limiter le nombre de requêtes
      (lutte contre les abus) et figure dans les journaux techniques du serveur. Ce qui est
      gardé, et combien de temps, est dans la
      <a href="/confidentialite">politique de confidentialité</a>.
    </p>
    <p>
      Le site interroge l'API avec une clé publique commune à tous les visiteurs, intégrée au
      site : aucune clé API personnelle n'est nécessaire pour le consulter.
    </p>
  </Panneau>

  <Panneau titre="Sources de données">
    <p>
      Les données viennent de producteurs publics et restent sous leur licence. Les principales :
    </p>
    <ul class="puces">
      {#each SOURCES_PRINCIPALES as source (source.donnees)}
        <li>
          {source.donnees} : {source.producteur}, {source.licence}{#if source.mention}{' '}(mention : {source.mention}){/if}.
        </li>
      {/each}
    </ul>
    <p>
      La liste complète, avec la licence, la période et la fraîcheur de chaque jeu servi, est sur
      <a href="/datasets">Données &amp; méthode</a>.
      Pour réutiliser une valeur : {ATTRIBUTION.charAt(0).toLowerCase() + ATTRIBUTION.slice(1)}
    </p>
  </Panneau>

  <Panneau titre="Ce que le site ne montre pas, signalements">
    <p>
      Le site ne montre ni acheteur, ni vendeur, ni occupant, ni le prix d'une vente isolée
      récente ; la liste et la base de chaque limite sont sur
      <a href={CHEMIN_CE_QUE_NOUS_NE_MONTRONS_PAS}>Ce que nous ne montrons pas</a>.
      Pour signaler une erreur ou une information qui vous concerne, écrivez à
      <a href="mailto:contact@datacommune.fr">contact@datacommune.fr</a>
      avec l'adresse de la page.
    </p>
  </Panneau>

  <Panneau titre="Code source">
    <p>
      Le code source de DataCommune est diffusé sous la licence GNU Affero General Public
      License, version 3 ou toute version ultérieure (AGPL-3.0-or-later), dans le dépôt public
      <a href={CODE_SOURCE_URL}>github.com/DataCommune/datacommune</a>. Chaque page servie correspond à
      un commit de ce dépôt.
    </p>
  </Panneau>

  <Panneau titre="Propriété intellectuelle">
    <p>
      Les textes et le design de ce site sont la propriété de l'éditeur, sauf mention
      contraire ; les données restent celles de leurs producteurs. La marque DataCommune et le logo
      associé sont des créations originales.
    </p>
  </Panneau>

  <Panneau titre="Documents associés">
    <ListeLiens
      liens={[
        { href: '/cgu', titre: "Conditions Générales d'Utilisation" },
        { href: '/confidentialite', titre: 'Politique de Confidentialité' }
      ]}
    />
  </Panneau>

</PageSimple>

<style>
  /* Key / value pairs (UI-SPEC § 3.13, `style.css:244-250`): two columns, a pale line between rows, one
     column up to 720 px. The key in Inter 400, the value as written in the text. */
  .tab {
    margin: var(--s4) 0 0;
  }

  .tab-l {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1.2fr);
    gap: var(--s4);
    padding: var(--s3) 0;
  }

  .tab-l + .tab-l {
    border-top: 1px solid var(--color-filet-pale);
  }

  .tab-l dt {
    font-weight: 400;
  }

  .tab-l dd {
    margin: 0;
    font-family: var(--font-display);
    font-size: 20px;
    font-weight: 500;
    line-height: 1.3;
  }

  @media (max-width: 720px) {
    .tab-l {
      grid-template-columns: minmax(0, 1fr);
      gap: var(--s1);
    }
  }
</style>
