<script lang="ts">
  // 04.8-06 (UI-SPEC § 6.9, D-13): v4 form, one simple panel per section; the text is compared word for word
  // to `tests/e2e/fixtures/textes-information-reference.json`. Changed on 2026-11-10 with the owner's agreement
  // (inventory I4 to I6, I10): table of contents, acronyms spelled out, no return link, date under the title.
  import MetaPartage from '$lib/components/ds/MetaPartage.svelte';
  import PageSimple from '$lib/components/ds/PageSimple.svelte';
  import Panneau from '$lib/components/ds/Panneau.svelte';
  import Sommaire from '$lib/components/ds/Sommaire.svelte';

  // Table of contents (I5, 2026-11-10): the titles as written, linked to the `id` of their panel.
  const SECTIONS = [
    { id: "section-1", titre: "1. Responsable du traitement" },
    { id: "section-2", titre: "2. Données traitées" },
    { id: "section-3", titre: "3. Finalités du traitement" },
    { id: "section-4", titre: "4. Base légale" },
    { id: "section-5", titre: "5. Durée de conservation" },
    { id: "section-6", titre: "6. Destinataires des données" },
    { id: "section-7", titre: "7. Transferts hors UE" },
    { id: "section-8", titre: "8. Cookies et traceurs" },
    { id: "section-9", titre: "9. Vos droits" },
    { id: "section-10", titre: "10. Modification de la politique" }
  ];
</script>

<MetaPartage titre="Politique de Confidentialité — DataCommune" description="Politique de confidentialité et protection des données personnelles de DataCommune." chemin="/confidentialite" />

<PageSimple titre="Politique de Confidentialité" ariane="Confidentialité" sousTitre="Dernière mise à jour : 11 novembre 2026">
  <Sommaire sections={SECTIONS} />

  <Panneau titre="1. Responsable du traitement" id="section-1">
    <p>
      Le responsable du traitement des données personnelles décrit ici est Ianis P.,
      particulier, éditeur du site DataCommune (projet personnel).
    </p>
    <p>
      Contact :
      <a href="mailto:contact@datacommune.fr">contact@datacommune.fr</a>
    </p>
  </Panneau>

  <Panneau titre="2. Données traitées" id="section-2">
    <p>
      Le site n'a pas de comptes : il ne demande ni adresse email, ni mot de passe, ni
      moyen de paiement, et il n'y a aucun paiement.
    </p>
    <ul class="puces">
      <li>
        <strong>Adresse IP</strong> : celle du visiteur est traitée pour limiter le nombre
        de requêtes (lutte contre les abus) et figure dans les journaux techniques du
        serveur. Pour chaque requête, l'API y inscrit la date, l'adresse IP et le type de
        requête (par exemple « fiche d'une adresse »), jamais l'adresse ni la commune
        consultée. Une erreur du site peut aussi y inscrire le chemin de la page en cause.
      </li>
      <li>
        <strong>Recherches</strong> : l'adresse, la commune ou le code postal saisis dans la
        recherche sont envoyés à l'API pour afficher les résultats.
      </li>
      <li>
        <strong>Stockage dans votre navigateur</strong> : la sélection de la page « Comparer »
        (stockage de l'onglet) et la liste des dernières communes consultées (stockage local)
        restent dans votre navigateur et disparaissent quand vous fermez l'onglet ou videz les
        données du site. La liste des communes consultées n'est envoyée à aucun serveur. La
        sélection ne l'est pas non plus en tant que telle, mais lancer une comparaison envoie à
        l'API les codes des communes, adresses ou documents choisis, comme une recherche.
      </li>
      <li>
        <strong>Cookie de session</strong> : à l'ouverture d'une fiche d'adresse, le site
        dépose un cookie technique (<code>dc_session</code>), un identifiant tiré au hasard
        qui ne contient aucune information sur vous. Il sert à compter les fiches différentes
        ouvertes dans la semaine, pour la limite de consultation gratuite (<a href="#section-8">section 8</a>).
      </li>
      <li>
        <strong>Mesure d'audience</strong> : à chaque page affichée, le site envoie à son
        propre serveur le chemin de la page, réduit à sa rubrique quand il désigne une adresse
        ou un document (« /adresse » pour toute fiche d'adresse, « /dpe » pour tout document
        DPE, diagnostic de performance énergétique), sans recherche ni paramètre. Un lien vers le site diffusé par
        l'éditeur lui-même peut porter le nom d'un canal, par exemple « reddit » ou « mail » : le site
        compte alors une arrivée par ce canal, sans identifiant, et seulement si ce nom figure dans une
        liste fixée d'avance. Le logiciel libre GoatCounter, installé chez
        l'éditeur, en tire des nombres de pages vues. Ni votre adresse IP, ni la page d'où vous
        venez, ni le titre de la page ne lui parviennent.
      </li>
      <li>
        <strong>Erreurs techniques</strong> : les erreurs du site et de l'API sont
        journalisées sur l'infrastructure de l'éditeur. Aucun service tiers de supervision
        des erreurs n'est utilisé.
      </li>
    </ul>
    <p>
      Aucun cookie de suivi n'est utilisé. Aucun pixel publicitaire n'est présent sur le
      site. Voir la <a href="#section-8">section 8</a>.
    </p>
  </Panneau>

  <Panneau titre="3. Finalités du traitement" id="section-3">
    <ul class="puces">
      <li><strong>Limitation du nombre de requêtes</strong> : compter les requêtes par adresse IP pour protéger le service d'un usage abusif.</li>
      <li><strong>Limite de consultation des fiches</strong> : compter, par cookie de session, les fiches d'adresse différentes ouvertes dans la semaine.</li>
      <li><strong>Mesure d'audience</strong> : savoir quelles pages servent et par quels liens diffusés par l'éditeur les lecteurs arrivent, en nombre de pages vues, pour améliorer le site.</li>
      <li><strong>Sécurité et diagnostic</strong> : retrouver l'origine d'une panne ou d'une attaque dans les journaux techniques.</li>
    </ul>
  </Panneau>

  <Panneau titre="4. Base légale" id="section-4">
    <p>
      Intérêt légitime de l'éditeur : assurer la sécurité du service, prévenir les abus et
      mesurer l'audience du site (article 6.1.f du RGPD).
    </p>
  </Panneau>

  <Panneau titre="5. Durée de conservation" id="section-5">
    <p>
      Le journal des requêtes de l'API (date, type de requête, adresse IP) est purgé une fois
      par semaine des lignes de plus de 7 jours : une ligne y reste au plus 14 jours.
    </p>
    <p>
      Les journaux techniques des services, qui peuvent contenir une adresse IP, sont limités en
      taille : trois fichiers de 10 Mo par service, le plus ancien étant effacé quand un nouveau
      commence. Leur durée de conservation dépend donc du trafic ; elle n'est pas fixée en jours.
    </p>
    <p>
      Le cookie de session expire au bout de 7 jours. Le décompte des fiches qui lui est
      associé est gardé en mémoire par le serveur, sans être écrit sur disque, et s'efface au
      plus tard 7 jours après la première fiche ouverte.
    </p>
    <p>
      Les statistiques de la mesure d'audience sont des nombres de pages vues, par page et par
      heure, gardés deux ans au plus. Un navigateur piloté par un programme n'envoie rien ; une
      requête que GoatCounter reconnaît lui-même comme venant d'un robot est gardée à part avec
      l'identification de son navigateur (User-Agent), puis effacée par la purge quotidienne qui
      suit ses 30 jours.
    </p>
  </Panneau>

  <Panneau titre="6. Destinataires des données" id="section-6">
    <p>
      Les journaux sont lus par l'éditeur seul. Une société intervient dans l'acheminement
      des requêtes :
    </p>
    <ul class="puces">
      <li>
        <strong>Cloudflare, Inc.</strong> (101 Townsend St, San Francisco, CA 94107, États-Unis) :
        réseau et tunnel d'accès au site et à l'API. Le trafic passe par ses serveurs, qui
        voient donc les requêtes et les adresses IP.
        <a href="https://www.cloudflare.com/trust-hub/gdpr/" target="_blank" rel="noopener noreferrer">Cloudflare et le RGPD</a>.
      </li>
    </ul>
    <p>
      La mesure d'audience est faite chez l'éditeur, par un logiciel installé sur son
      infrastructure : ses statistiques ne sont transmises à personne. Aucune donnée n'est
      vendue à des tiers. Aucun partenaire publicitaire n'a accès aux données.
    </p>
    <p>
      <strong>Carte de l’en-tête d’une fiche d’adresse</strong> : c’est une image du Plan IGN (Institut national de l’information géographique et forestière) et du
      Parcellaire Express, récupérée et mise en cache par notre serveur. Pour l’afficher, votre
      navigateur ne contacte jamais l’IGN, et aucune donnée vous concernant (adresse IP, en-têtes,
      cookies) ne lui est transmise. Le lien « Ouvrir dans le Géoportail » mène au site de l’IGN :
      si vous le suivez, c’est votre navigateur qui le contacte, avec la position de l’adresse. Quand une carte n’est pas encore en cache, l’IGN peut apprendre qu’une
      adresse est consultée à un moment donné, mais pas par qui.
    </p>
  </Panneau>

  <Panneau titre="7. Transferts hors UE" id="section-7">
    <p>
      Le site et l'API sont hébergés en France, chez l'éditeur. Cloudflare est une société
      américaine : le trafic qui transite par son réseau peut être traité hors de l'Union
      européenne, selon les garanties décrites par Cloudflare (lien en <a href="#section-6">section 6</a>).
    </p>
  </Panneau>

  <Panneau titre="8. Cookies et traceurs" id="section-8">
    <p>
      Le site dépose un seul cookie, <code>dc_session</code>, et seulement à l'ouverture d'une
      fiche d'adresse : il porte la limite de consultation gratuite des fiches (un nombre
      limité de fiches différentes par semaine). C'est un cookie technique, nécessaire au
      service et lu par lui seul ; à ce titre, il ne demande pas de consentement (lignes
      directrices de la CNIL (Commission nationale de l’informatique et des libertés) sur les traceurs). Il n'y a ni pixel publicitaire, ni partage de
      données avec une régie ou un réseau social.
    </p>
    <p>
      <strong>Mesure d'audience</strong> : elle ne dépose aucun cookie et n'écrit rien dans
      votre navigateur. Elle est faite par GoatCounter, logiciel libre installé chez l'éditeur,
      sur le domaine du site : aucun script ni service tiers n'est chargé. Elle respecte les
      conditions que la CNIL pose à une mesure d'audience dispensée de consentement : une
      finalité limitée à la mesure de l'audience du site, pour l'éditeur seul, des statistiques
      anonymes, aucun recoupement avec d'autres traitements ni transmission à un tiers, aucun
      suivi de votre navigation sur d'autres sites
      (<a href="https://www.cnil.fr/fr/cookies-et-autres-traceurs/regles/cookies-solutions-pour-les-outils-de-mesure-daudience" target="_blank" rel="noopener noreferrer">CNIL, solutions pour la mesure d'audience</a>).
    </p>
  </Panneau>

  <Panneau titre="9. Vos droits" id="section-9">
    <p>
      Conformément au Règlement Général sur la Protection des Données (RGPD),
      vous disposez des droits suivants :
    </p>
    <ul class="puces">
      <li><strong>Droit d'accès</strong> : obtenir une copie de vos données personnelles.</li>
      <li><strong>Droit de rectification</strong> : corriger des données inexactes ou incomplètes.</li>
      <li><strong>Droit à l'effacement</strong> : demander la suppression de vos données.</li>
      <li><strong>Droit d'opposition</strong> : vous opposer au traitement de vos données.</li>
      <li><strong>Droit à la limitation</strong> : restreindre le traitement de vos données.</li>
    </ul>
    <p>
      Pour exercer ces droits, contactez-nous à :
      <a href="mailto:contact@datacommune.fr">contact@datacommune.fr</a>.
      Une adresse IP seule ne permet de retrouver vos données que si vous la communiquez.
      Délai de réponse : 30 jours maximum.
    </p>
    <p>
      En cas de réclamation, vous pouvez saisir la Commission Nationale de
      l'Informatique et des Libertés (CNIL) :
      <a href="https://www.cnil.fr" target="_blank" rel="noopener noreferrer">www.cnil.fr</a>.
    </p>
  </Panneau>

  <Panneau titre="10. Modification de la politique" id="section-10">
    <p>
      Cette politique peut évoluer, notamment si des comptes sont ajoutés au site ou si la
      mesure d'audience change. La date de dernière mise à jour est affichée en haut de cette page.
    </p>
  </Panneau>
</PageSimple>
