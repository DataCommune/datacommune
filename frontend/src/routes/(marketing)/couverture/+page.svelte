<script lang="ts">
  // 04.8-06 (UI-SPEC § 6.9, D-13): v4 form, one simple panel per section; the text is compared word for
  // word to `tests/e2e/fixtures/textes-information-reference.json`. Owner decision of 2026-11-09: the
  // coverage is the whole country since the national switch of 2026-11-02 (reference amended with it).
  import MetaPartage from '$lib/components/ds/MetaPartage.svelte';
  import PageSimple from '$lib/components/ds/PageSimple.svelte';
  import Panneau from '$lib/components/ds/Panneau.svelte';
</script>

<MetaPartage titre="Couverture et limites — DataCommune" description="Ce que DataCommune couvre aujourd'hui (France entière, 101 départements) et les limites connues de ses données." chemin="/couverture" />

<PageSimple titre="Couverture et limites" ariane="Couverture et limites" sousTitre="Dernière mise à jour : novembre 2026">
  <Panneau titre="Ce que couvre le site">
    <p>
      Les données sont servies pour toute la France : 101 départements, outre-mer compris.
      Chaque jeu de données a sa propre couverture : les ventes (DVF) ne couvrent ni la Moselle,
      ni le Bas-Rhin, ni le Haut-Rhin, ni Mayotte.
    </p>
    <p>
      Quand un jeu ne couvre pas un lieu, la fiche affiche « hors couverture ». Cela veut dire
      que le site ne sait pas, pas que la valeur est nulle : une absence de couverture ne vaut
      jamais zéro.
    </p>
    <p>
      Le détail de chaque jeu de données (période, fraîcheur, licence) est sur la page
      <a href="/datasets">Données &amp; méthode</a>.
    </p>
  </Panneau>

  <Panneau titre="Limites connues">
    <ul class="puces">
      <li>
        <strong>Des adresses sans ventes rattachées.</strong> Une vente est rattachée à une
        adresse par la parcelle cadastrale qui contient le point de l'adresse. Quand ce point
        ne tombe dans aucune parcelle, l'adresse n'a pas de ventes rattachées. C'est le cas
        d'environ 24 % des adresses en Loire-Atlantique et 38 % en Vendée ; la part nationale
        n'est pas mesurée. Pour ces adresses,
        « pas de ventes affichées » ne veut pas dire « pas de ventes ».
      </li>
      <li>
        <!-- C3 (04.5-08): the rule the pages serve (number and suffix); to be read in a block by Ianis (R10). -->
        <strong>Le numéro et son suffixe sont distingués.</strong> Les ventes déclarées à une
        adresse y sont rattachées par le numéro et le suffixe déclarés dans les demandes de
        valeurs foncières (DVF) : celles du « 2 bis » ne figurent pas sur la page du « 2 ».
        Quand un suffixe ne correspond à aucune adresse, ou à plusieurs, la vente n'est montrée
        sur aucune.
      </li>
      <li>
        <!-- C4 (04.5-07): VEFA listed and marked, never counted; to be read in a block by Ianis (R10). -->
        <strong>Les ventes en l'état futur d'achèvement (VEFA) déclarées à une adresse sont listées et marquées
        « VEFA, hors statistiques »</strong>, mais ne sont pas comptées.
      </li>
      <li>
        <strong>Un DPE est un document, pas un logement.</strong> Un diagnostic de performance
        énergétique est un document rattaché à une adresse. Ce n'est pas une mesure du
        logement que vous consultez, et le nombre de DPE d'une adresse n'est pas un nombre de
        logements.
      </li>
    </ul>
  </Panneau>

  <Panneau titre="Les sources">
    <p>Les données viennent de sources publiques ouvertes :</p>
    <ul class="puces">
      <li>
        Ventes (DVF), DGFiP / Etalab :
        <a
          href="https://www.data.gouv.fr/datasets/demandes-de-valeurs-foncieres-geolocalisees"
          target="_blank"
          rel="noopener noreferrer">Demandes de valeurs foncières géolocalisées</a>
      </li>
      <li>
        Adresses (BAN), DINUM / IGN :
        <a
          href="https://adresse.data.gouv.fr"
          target="_blank"
          rel="noopener noreferrer">Base Adresse Nationale</a>
      </li>
      <li>
        Parcelles (cadastre), DGFiP / Etalab :
        <a
          href="https://cadastre.data.gouv.fr"
          target="_blank"
          rel="noopener noreferrer">Cadastre Etalab</a>
      </li>
      <li>
        Diagnostics énergétiques (DPE), ADEME :
        <a
          href="https://data.ademe.fr/datasets/dpe03existant"
          target="_blank"
          rel="noopener noreferrer">DPE logements existants</a>
      </li>
      <li>
        Bâtiments, beta.gouv.fr / CSTB :
        <a
          href="https://rnb.beta.gouv.fr"
          target="_blank"
          rel="noopener noreferrer">Référentiel national des bâtiments (RNB)</a>
      </li>
    </ul>
    <p>
      Les autres jeux de données (démographie, risques, équipements, etc.) et leurs licences
      sont listés sur
      <a href="/datasets">Données &amp; méthode</a>.
    </p>
  </Panneau>

  <Panneau titre="Une erreur ou un doute">
    <p>
      Écrivez à
      <a href="mailto:contact@datacommune.fr">contact@datacommune.fr</a>
      en indiquant l'adresse concernée.
    </p>
  </Panneau>
</PageSimple>
