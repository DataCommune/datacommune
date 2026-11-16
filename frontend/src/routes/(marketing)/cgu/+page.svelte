<script lang="ts">
  // 04.8-06 (UI-SPEC § 6.9, D-13): v4 form, one simple panel per section; the text is compared word for word
  // to `tests/e2e/fixtures/textes-information-reference.json`. Changed on 2026-11-10 with the owner's agreement
  // (inventory I4 to I6, I10): table of contents, acronyms spelled out, no return link, date under the title.
  import MetaPartage from '$lib/components/ds/MetaPartage.svelte';
  import { CODE_SOURCE_URL } from '$lib/data/ouverture';
  import PageSimple from '$lib/components/ds/PageSimple.svelte';
  import Panneau from '$lib/components/ds/Panneau.svelte';
  import Sommaire from '$lib/components/ds/Sommaire.svelte';

  // Table of contents (I5, 2026-11-10): the titles as written, linked to the `id` of their panel.
  const SECTIONS = [
    { id: "section-1", titre: "1. Objet et définitions" },
    { id: "section-2", titre: "2. Conditions d'accès" },
    { id: "section-3", titre: "3. Description du service" },
    { id: "section-4", titre: "4. Obligations de l'utilisateur" },
    { id: "section-5", titre: "5. Propriété intellectuelle" },
    { id: "section-6", titre: "6. Responsabilité et garanties" },
    { id: "section-7", titre: "7. Tarification" },
    { id: "section-8", titre: "8. Modification et résiliation" },
    { id: "section-9", titre: "9. Droit applicable et litiges" },
    { id: "section-10", titre: "10. Date d'entrée en vigueur" }
  ];
</script>

<MetaPartage titre="Conditions Générales d'Utilisation — DataCommune" description="Conditions générales d'utilisation du site et de l'API DataCommune." chemin="/cgu" />

<PageSimple titre="Conditions Générales d'Utilisation" ariane="CGU" sousTitre="Dernière mise à jour : 9 novembre 2026">
  <Sommaire sections={SECTIONS} />

  <Panneau titre="1. Objet et définitions" id="section-1">
    <p>
      Les présentes Conditions Générales d'Utilisation (ci-après « CGU ») régissent
      l'accès et l'utilisation du service DataCommune (ci-après « le Service »).
    </p>
    <p>
      DataCommune est un site, adossé à une API REST, qui rassemble des jeux de données
      publiques françaises. Son périmètre géographique actuel est décrit sur la page
      <a href="/couverture">Couverture et limites</a>.
    </p>
    <ul class="puces">
      <li><strong>API</strong> : interface de programmation applicative accessible via des requêtes HTTP.</li>
      <li><strong>Utilisateur</strong> : toute personne physique ou morale qui consulte le site ou appelle l'API.</li>
      <li><strong>Service</strong> : le site et l'API DataCommune.</li>
      <li><strong>Données publiques</strong> : données issues de sources officielles (data.gouv.fr, IGN (Institut national de l’information géographique et forestière), ADEME (Agence de la transition écologique), Arcep, INSEE, Géorisques) mises à disposition sous licence ouverte ou licence spécifique du producteur.</li>
    </ul>
  </Panneau>

  <Panneau titre="2. Conditions d'accès" id="section-2">
    <p>
      Le Service est ouvert à tous, sans compte ni inscription. Le site interroge l'API avec une
      clé publique commune à tous les visiteurs.
    </p>
    <p>
      Le nombre de requêtes est limité par adresse IP pour protéger le Service des abus. Une
      fois la limite atteinte, l'API répond 429 avec un délai d'attente.
    </p>
    <p>
      Sur le site, la consultation gratuite des fiches d'adresse est limitée à 100 fiches
      différentes par période de 7 jours, la période commençant à la première fiche ouverte. Le
      décompte est tenu par un cookie de session (voir la
      <a href="/confidentialite">politique de confidentialité</a>).
      Une fiche déjà ouverte reste consultable. Les robots d'indexation vérifiés ne sont pas
      concernés.
    </p>
  </Panneau>

  <Panneau titre="3. Description du service" id="section-3">
    <p>
      DataCommune agrège plusieurs jeux de données publiques françaises — géocodage, cadastre,
      transactions immobilières (DVF, Demandes de valeurs foncières), diagnostics énergétiques (DPE), loyers, risques
      naturels, urbanisme (PLU, plan local d’urbanisme), bâtiments, monuments historiques, connectivité,
      entreprises, carte scolaire et transports — en une seule API REST.
    </p>
    <p>
      Les données proviennent de sources officielles : data.gouv.fr, IGN, ADEME, Arcep,
      INSEE et Géorisques. Le Service est fourni « en l'état », sur la base du meilleur
      effort.
    </p>
  </Panneau>

  <Panneau titre="4. Obligations de l'utilisateur" id="section-4">
    <p>L'Utilisateur s'engage à :</p>
    <ul class="puces">
      <li>Citer la source de chaque donnée réutilisée (son producteur) et la date de sa dernière mise à jour, indiquées sur <a href="/datasets">Données &amp; méthode</a>, et respecter la licence de ce producteur. La réutilisation, y compris commerciale, est permise dans ce cadre. Mentionner DataCommune, avec le lien de la page, est bienvenu sans être une condition.</li>
      <li>Pour les ventes immobilières (DVF) : ne les réutiliser que pour concourir à la transparence des marchés fonciers et immobiliers, comme le demande leur producteur ; ne pas les réutiliser d'une manière qui ait pour objet ou pour effet de permettre la réidentification des personnes concernées, et prendre les mesures qui empêchent leur indexation par un moteur de recherche externe (article R*112 A-3 du livre des procédures fiscales).</li>
      <li>Respecter les limites de requêtes et ne pas chercher à les contourner.</li>
      <li>Respecter une politique d'usage raisonnable (fair use).</li>
      <li>Se conformer aux lois et réglementations applicables lors de l'utilisation des données.</li>
    </ul>
  </Panneau>

  <Panneau titre="5. Propriété intellectuelle" id="section-5">
    <p>
      Le design, la documentation et le contenu éditorial du Service sont la propriété de
      l'éditeur, sauf mention contraire.
    </p>
    <p>
      <strong>Code source</strong> : le code source du Service est diffusé sous la licence GNU
      Affero General Public License, version 3 ou toute version ultérieure (AGPL-3.0-or-later),
      dans le dépôt public <a href={CODE_SOURCE_URL}>github.com/DataCommune/datacommune</a>.
    </p>
    <p>
      Les données servies par l'API restent sous la licence de leur producteur, nommée pour
      chaque jeu de données sur
      <a href="/datasets">Données &amp; méthode</a>
      (le plus souvent la Licence Ouverte). L'Utilisateur est tenu de respecter les conditions
      de chaque licence.
    </p>
    <p>
      L'Utilisateur dispose d'un droit d'utilisation non exclusif et non cessible
      du Service, dans les limites des présentes CGU.
    </p>
  </Panneau>

  <Panneau titre="6. Responsabilité et garanties" id="section-6">
    <p>
      Le Service est fourni sur la base du meilleur effort, sans garantie de
      disponibilité permanente. Aucun accord de niveau de service (SLA) n'est garanti.
    </p>
    <p>
      DataCommune ne saurait être tenu responsable de l'exactitude des données servies,
      celle-ci dépendant des sources publiques d'origine. Les données affichées ont
      des limites connues, décrites sur la page
      <a href="/couverture">Couverture et limites</a>.
      Le Service peut être interrompu à tout moment, notamment pour maintenance.
    </p>
  </Panneau>

  <Panneau titre="7. Tarification" id="section-7">
    <p>
      Le Service est gratuit, dans le cadre d'un usage raisonnable. Aucun paiement n'est
      demandé ni possible.
    </p>
  </Panneau>

  <Panneau titre="8. Modification et résiliation" id="section-8">
    <p>
      L'éditeur peut modifier les présentes CGU ; la version en vigueur est celle publiée sur
      cette page, avec sa date de mise à jour. La poursuite de l'utilisation du Service après
      une modification vaut acceptation des nouvelles conditions. Pour toute question :
      <a href="mailto:contact@datacommune.fr">contact@datacommune.fr</a>.
    </p>
    <p>
      L'éditeur peut bloquer l'accès d'une adresse IP en cas de violation des présentes CGU
      ou d'usage abusif.
    </p>
  </Panneau>

  <Panneau titre="9. Droit applicable et litiges" id="section-9">
    <p>
      Les présentes CGU sont régies par le droit français. En cas de litige, les
      parties s'engagent à rechercher une résolution amiable préalablement à toute
      action judiciaire.
    </p>
    <p>
      À défaut de résolution amiable, les tribunaux compétents sont déterminés selon les
      règles de droit commun.
    </p>
  </Panneau>

  <Panneau titre="10. Date d'entrée en vigueur" id="section-10">
    <p>
      Les présentes CGU sont en vigueur depuis octobre 2026.
    </p>
  </Panneau>
</PageSimple>
