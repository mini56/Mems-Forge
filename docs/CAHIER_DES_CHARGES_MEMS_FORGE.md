# Cahier des charges — MEMS Forge

## 1. Objet

MEMS Forge construit et maintient la future base documentaire destinée à MEMS Manager V2.

Chaîne imposée :

**PDF original → identification → lecture documentaire → extraction brute → reconstruction géométrique → interprétation → provenance → contrôles → validation humaine si nécessaire → pack → intégration atomique → base publiée.**

## 2. Règle centrale du prélèvement PDF

MEMS Forge ne doit pas se limiter à extraire du texte. Il doit lire chaque page comme un document en combinant :

- les objets PDF natifs : texte, spans, bbox, polices, images, vecteurs ;
- le rendu visuel de la page : colonnes, titres, tableaux, encadrés, procédures, notes, légendes, schémas, ordre de lecture.

La fidélité au PDF source prime sur la quantité de données extraites.

Une information ne doit pas être simplifiée, fusionnée, traduite ou structurée tant que sa position, son ordre, son contexte et sa provenance ne sont pas suffisamment établis.

## 3. Source et révision physique

Chaque fichier PDF est une révision physique distincte dès que son SHA-256 diffère, même si son nom est identique à celui d'un autre fichier.

Pour chaque source, MEMS Forge conserve au minimum :

- nom du fichier ;
- SHA-256 ;
- taille ;
- nombre de pages ;
- langue ;
- type documentaire ;
- référence constructeur lorsqu'elle est identifiable ;
- édition/révision lorsqu'elle existe ;
- version du moteur MEMS Forge utilisée.

Une pagination appartient toujours à une révision binaire précise.

## 4. Extraction brute

MEMS Forge conserve avant interprétation :

- texte exact ;
- page physique ;
- bbox du bloc ;
- bbox des spans ;
- police ;
- taille ;
- graisse ;
- orientation si disponible ;
- ordre géométrique ;
- méthode d'extraction.

La géométrie ne doit jamais être détruite avant l'interprétation sémantique.

## 5. Structure documentaire et ordre de lecture

MEMS Forge doit détecter et contrôler notamment :

- une ou plusieurs colonnes ;
- colonnes verticalement décalées ;
- lignes pleine largeur ;
- titres et sous-titres ;
- tableaux ;
- encadrés ;
- notes ;
- procédures ;
- illustrations et légendes.

Une ligne pleine largeur ne doit pas à elle seule casser un flux gauche/droite.

Une suite numérotée doit être contrôlée après reconstruction.

## 6. Procédures

MEMS Forge doit pouvoir distinguer :

- opération ;
- sous-opération ;
- phase ;
- étape ;
- instruction ;
- note ;
- warning/caution ;
- référence ;
- illustration associée.

Un nombre n'est jamais considéré automatiquement comme un numéro d'étape.

Les séquences doivent être contrôlées : trous, doublons, retour en arrière, numéro 0 anormal, changement d'opération, changement de phase, contamination entre pages, mauvaise frontière.

## 7. Tableaux

Les tableaux doivent rester structurés. MEMS Forge conserve :

- lignes ;
- colonnes ;
- cellules ;
- intitulés ;
- valeurs ;
- unités ;
- tolérances ;
- conditions ;
- géométrie.

Un tableau ne doit pas être aplati en texte linéaire avant interprétation.

## 8. Notices

WARNING, CAUTION et NOTE sont des objets explicites et doivent être rattachés à leur véritable portée : document, section, opération, phase ou étape.

## 9. Renvois

Le texte original du renvoi est conservé. La cible résolue est une relation distincte. Toute cible ambiguë reste non résolue jusqu'à validation.

## 10. OCR

MEMS Forge distingue :

- page blanche réelle ;
- page texte native ;
- page raster ;
- page mixte ;
- page nécessitant OCR.

Pour une zone OCR sont conservés : image source, page, bbox, langue, texte brut, confiance, moteur et version.

Une page ne doit jamais être déclarée blanche uniquement parce que l'extraction native est vide.

## 11. Visuels

Texte et visuels constituent deux couches distinctes.

Raster : filtrage des pictogrammes, logos et ressources répétitives ; bbox exacte, hash, dimensions et provenance conservés.

Vectoriel : pipeline séparé, regroupement déterministe, aucune union page entière pouvant fusionner des dessins indépendants.

## 12. Applicabilité des données techniques

Toute donnée technique publiée doit posséder un domaine d'applicabilité déterminé à partir de la source.

Ce domaine peut comprendre, selon ce que démontre le document :

- constructeur ;
- modèle ;
- génération/variante ;
- année/période ;
- moteur ;
- cylindrée ;
- injection ;
- famille ECU ;
- version ECU ;
- marché/pays ;
- transmission ;
- équipement ;
- tout autre critère futur nécessaire.

La liste doit rester extensible.

MEMS Forge ne doit jamais généraliser une information au-delà du périmètre prouvé par la source.

## 13. Plusieurs sources pour une même information

Une même donnée technique peut posséder plusieurs provenances.

MEMS Forge doit distinguer :

- même information confirmée par plusieurs sources ;
- variante liée à l'applicabilité ;
- contradiction réelle ;
- correspondance encore incertaine.

Aucune fusion n'est autorisée sur la seule similarité textuelle.

Une contradiction ou une applicabilité incertaine devient une donnée à valider.

## 14. Socle permanent et extensible

Le socle doit garantir au minimum les fonctions suivantes :

- documents ;
- révisions physiques ;
- pages source ;
- blocs bruts ;
- spans bruts ;
- entités structurées ;
- provenance ;
- relations ;
- assets ;
- éléments à valider ;
- packs ;
- dépendances de packs ;
- migrations de schéma ;
- métadonnées de base ;
- applicabilité.

Les futures tables métier ne sont pas figées aujourd'hui.

## 15. Données incertaines et validation humaine

Toute donnée incertaine bloquante empêche la publication du pack.

Aucune correction silencieuse n'est autorisée.

La validation humaine doit conserver :

- proposition initiale ;
- décision ;
- correction éventuelle ;
- validateur ;
- date ;
- version MEMS Forge ;
- pack concerné.

Les décisions doivent rester auditables.

## 16. Database Tester

Un outil séparé, sans IA, doit permettre d'interroger directement une base ou un pack produit par MEMS Forge.

Fonctions minimales :

- recherche directe ;
- liste des résultats ;
- détail d'une donnée ;
- contexte d'applicabilité ;
- document/révision/page ;
- texte brut ;
- provenance ;
- état de validation ;
- accès à la source PDF lorsque disponible.

Le Database Tester doit afficher en permanence un indicateur visible :

**Données à valider : N**

avec accès direct à la file de validation.

États visuels recommandés :

- orange : validation requise ;
- rouge : blocage critique ;
- vert : aucune validation en attente.

Tant qu'une validation bloquante subsiste :

**PACK BLOQUÉ — VALIDATION REQUISE**

Le testeur reste un outil MEMS Forge et n'est pas intégré à MEMS Manager V2.

## 17. Packs

Chaque traitement produit un pack autonome candidat. Un pack ne modifie pas directement la base publiée.

Le pack déclare au minimum :

- identifiant ;
- format et version de format ;
- version de schéma ;
- version MEMS Forge ;
- SHA-256 ;
- taille ;
- sources ;
- données ;
- provenance ;
- assets ;
- anomalies ;
- validations ;
- dépendances ;
- éventuelles règles de supersession.

Le fichier réellement livré est revérifié après transport et assemblage.

## 18. Intégration

Principe : **Base N + pack validé = Base N+1**.

L'application se fait sur une copie temporaire puis tous les contrôles sont relancés.

Deux résultats seulement sont admis :

- succès total ;
- aucune modification de la base active.

L'ordre d'application est défini par manifeste, jamais par nom de fichier, tri alphabétique, ordre de commit ou découverte sur disque.

## 19. Évolution du schéma

La base est extensible. Toute évolution structurelle passe par une migration versionnée et contrôlée.

Une migration ne doit pas invalider les anciennes données valides.

## 20. Multilingue

Langues prévues pour MEMS Manager V2 :

- français `fr` ;
- anglais `en` ;
- italien `it` ;
- espagnol `es` ;
- allemand `de` ;
- portugais `pt` ;
- japonais `ja` ;
- hindi `hi`.

La langue source est toujours conservée.

Les documents constructeur en langues différentes restent des révisions/sources distinctes et ne sont jamais alignés uniquement par numéro de page.

Les traductions techniques constituent une couche séparée reliée aux données source. MEMS Manager V2 choisit la langue à afficher ; il ne doit pas traduire techniquement la base à la volée.

## 21. Publication et MEMS Manager V2

MEMS Manager V2 consomme seulement une base validée et publiée.

Architecture visée :

- base locale fonctionnelle ;
- manifeste distant léger ;
- vérification de version au démarrage ;
- téléchargement uniquement si une version plus récente existe ;
- contrôle SHA-256, taille, format, version et compatibilité ;
- activation atomique ;
- conservation de la version précédente ;
- fonctionnement hors ligne toujours possible.

Le canal de distribution exact pourra être GitHub et sera figé lors de l'implémentation de la publication.

## 22. Validation d'une version MEMS Forge

Une nouvelle version du moteur ne doit pas être déclarée valide uniquement parce qu'elle compile ou produit une base SQLite correcte.

Elle doit être testée sur un corpus PDF représentatif incluant notamment : colonnes, procédures multi-pages, tableaux, raster/OCR, mixte, vectoriel, visuels, langues et variantes documentaires.

Le corpus de test sera fourni séparément.
