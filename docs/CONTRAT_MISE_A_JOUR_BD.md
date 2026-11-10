# Contrat de mise à jour documentaire MEMS Forge → ECU MEMS Manager

## Résultat attendu

L'utilisateur ajoute un PDF dans `depot/`. MEMS Forge effectue l'extraction complète,
l'OCR si nécessaire, conserve le PDF et toutes les preuves de source, construit et
vérifie un pack. Après contrôle et autorisation de publication, un pack de données
versionné est mis à disposition indépendamment d'un Setup Windows. ECU MEMS Manager
signale le pack et l'installe dans les données locales, sans réinstaller l'application.

## Étapes et état réel

1. **Lecture PDF et extraction** : existant. `pdf-intake.yml` produit les comptes
   natifs, `full-document-audit.yml` l'OCR complet des PDF raster.
2. **Pack documentaire candidat** : existant. `document_pack.py` produit un ZIP
   contenant le PDF inchangé, SQLite par page, les occurrences visuelles et,
   avec le pipeline complet, le texte et les preuves OCR. Les SHA-256 et l'intégrité
   SQLite sont vérifiés, les éléments incertains ne sont pas des faits validés.
3. **Installation locale de prévisualisation** : existant. `local_update.py`
   installe sans écraser ni modifier la base de l'application, vérifie les doublons
   et permet une recherche avec provenance. Le marquage « non validé » est imposé.
4. **Validation pour publication** : **à terminer**. Contrôles sur OCR, pages,
   schémas, correspondance source, droits de diffusion et approbation explicite
   de chaque pack. Aucun pack non validé ne doit être publié comme connaissance.
5. **Distribution versionnée indépendante du Setup** : **partiellement réalisée**.
   La commande `draft-catalogue` génère un inventaire JSON déterministe des
   packs candidats, SHA-256, taille, document source, pages et dépendances.
   `verify-catalogue` détecte la modification d'un pack ou de l'inventaire.
   Le catalogue reste explicitement `DRAFT_NOT_PUBLISHED`, sans URL de
   téléchargement ni autorisation de diffusion. La publication dans une Release
   *documentaire* distincte de celle du programme reste **à terminer**.
6. **Réception par ECU MEMS Manager** : **à terminer**. Lecture du catalogue
   en ligne sur demande ; import ZIP local sans réseau ; vérification hash et
   format ; sauvegarde des versions précédentes ; remplacement atomique et
   retour arrière en cas d'échec. Ne jamais écrire dans Program Files ni dans
   les bases personnelles.
7. **Recherche commune avec l'IA** : **partiellement réalisée**. La commande
   `search-all` du testeur indépendant sait interroger en lecture seule les
   bases installées et les packs locaux de prévisualisation, en signalant
   l'origine de chaque résultat et le statut `UNVERIFIED_SOURCE_TEXT`.
   L'intégration à la page Base de données, à l'index de l'application, à
   l'ouverture des PDF et au moteur IA reste **à terminer**. Les contenus
   bruts non validés ne sont pas des faits techniques approuvés.

## Emplacements prévus

- Données livrées avec l'application : conserver sans modification
  `<dossier application>/database/reference/` et `MEMSLibrary_Pack_001/`.
- Packs ajoutés : dans `QStandardPaths::AppLocalDataLocation/document_updates/`,
  hors des fichiers exécutables et des bases utilisateur.
- Sous-dossiers individuels identifiés par SHA-256 du PDF source ; conserver
  la version précédente pour retour arrière.
- Ne pas déduire une ressource de son chemin textuel : le fichier doit exister,
  être haché et être ouvert à partir du pack autorisé.

## Conditions de réception avant activation de « Mise à jour en ligne »

- Un PDF réel placé dans `depot/` déclenche la chaîne sans opération technique
  supplémentaire de l'utilisateur ; les revues bloquantes empêchent seulement
  la publication définitive.
- Un pack neuf donne de nouveaux résultats dans la base installée, sans changer
  de Setup ; recherche et source PDF/pages accessibles.
- L'IA consulte les mêmes pages, ne fabrique pas de source et distingue
  « preuve brute OCR » / « donnée technique validée ».
- Un pack corrompu, incomplet, doublon conflictuel ou non approuvé est refusé.
- La base existante, les fichiers du programme et les données personnelles sont
  inchangés si l'installation échoue.
- Après redémarrage, les données restent accessibles hors ligne.
- Tests sur Windows sans installation précédente et avec base existante ;
  compatibilité avec les 14 pages, les styles figés et les résolutions validées.
- Publication automatique interdite avant passage de ces contrôles et preuve des
  droits de redistribution des PDF embarqués.

## État de la première démonstration

- Document AKM7169ENG : 482 pages, 482 jeux OCR, 96 060 mots OCR dans le pack,
  SHA-256 des membres correct, SQLite `integrity_check=ok`.
- Son OCR contient encore des textes mal reconnus : **pack candidat uniquement**.
- Aucun téléchargement ou mise à jour de la BD par ECU MEMS Manager n'est
  encore fonctionnel ; le libellé « mise à jour en ligne » reste désactivé.

## Vérifications ajoutées le 10 novembre 2026

- Pipeline OCR réorganisé par SHA-256 source : tous les PDF modifiés sont
  sélectionnés lors d'un dépôt GitHub, chacun dans son dossier d'audit,
  avec un pack candidat indépendant et un compte rendu par source.
- Les modifications de code seules ne relancent pas le long OCR des anciens PDF.
- L'absence de langue OCR reconnue reste bloquante pour le pack OCR complet :
  ne pas annoncer une extraction complète en cas d'échec.
- `mems-forge draft-catalogue --packs <dossier> --output <catalogue.json>`
  prépare un catalogue **local et non publié**.
- `mems-forge verify-catalogue --packs <dossier> --catalogue <catalogue.json>`
  contrôle l'inventaire sur les octets réels.
- `mems-forge search-all --install-root <installation> --store <depot-local>
  --query <texte>` combine les références existantes et les preuves PDF
  de prévisualisation, **sans modifier les données installées**.
- Tests automatisés GitHub : construction PDF, SHA-256, doublons, corruption,
  non-régression des bases existantes et provenance des pages.

**À ne pas confondre** : une suite de tests verte valide le fonctionnement
du traitement et du contrôle d'intégrité ; elle ne certifie pas la pertinence
technique ou les droits de redistribution d'un PDF. Aucun Setup ou pack
officiel n'a été publié par ces changements.
