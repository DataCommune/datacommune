# Sources publiques loyers 2025 figées

Quatre fichiers officiels, 34 900 lignes par fichier, 139 600 lignes au total.
Source : Carte des loyers, estimations ANIL à partir des annonces SeLoger et
leboncoin, ressources publiques du ministère disponibles sur data.gouv.fr.
Le reçu conservé dans `manifest.json` indique les URLs demandées et finales,
Last-Modified, acquisition du 8 septembre 2026, tailles et SHA256 bruts/compressés.
Cette acquisition ne constitue pas la date de publication des observations.

Les CSV contiennent 13 colonnes d'agrégats communaux : code/libellé communal,
EPCI, département, région, loyers prédits, intervalle, zone, échelle de prédiction,
nombres d'observations et R² ajusté. Aucune annonce individuelle, personne,
coordonnée d'un bien, clé ou donnée de compte n'est présente.

Compression gzip avec mtime=0 : 3 063 522 octets au total. Les quatre fichiers
ont été décompressés et comparés octet par octet aux originaux acquis ; les
empreintes sont vérifiées à nouveau par les tests. Un scan du texte décompressé
pour clés privées, Bearer, JWT, clés AWS et préfixes de credentials applicatifs
n'a trouvé aucun motif. Ce scan complète le contrôle du schéma public ; il n'est
pas une certification générale de détection de secrets.

`tests/oracles/loyers_full.py` borne la décompression à la taille attendue+1,
vérifie les hashes puis compare chaque tuple avec un oracle CSV/Decimal sans
import de code applicatif. Les types source EPCI sont au nombre de 671 :
608 maisons et 63 appartements de trois pièces ou plus.

Ces fichiers sont réservés aux tests et à la qualification du contrat d'entrée.
Ils ne constituent pas un manifeste opérationnel autorisant un import distant.
