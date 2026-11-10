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
5. **Distribution versionnée indépendante du Setup** : **à terminer**. Créer
   un catalogue JSON stable, version/identifiant, PDF source, hash SHA-256 du
   pack, taille, dépendances et date ; publication dans une Release *documentaire*
   distincte des Releases de l'application.
6. **Réception par ECU MEMS Manager** : **à terminer**. Lecture du catalogue
   en ligne sur demande ; import ZIP local sans réseau ; vérification hash et
   format ; sauvegarde des versions précédentes ; remplacement atomique et
   retour arrière en cas d'échec. Ne jamais écrire dans Program Files ni dans
   les bases personnelles.
7. **Recherche commune avec l'IA** : **à terminer**. Index unique consultable
   par la page Base de données et par le moteur documentaire IA, avec l'identité
   du PDF, la page, l'image, le statut de validation et une commande pour ouvrir
   le PDF. Les contenus non validés sont consultables comme sources brutes
   étiquetées, mais pas traités comme des spécifications techniques approuvées.

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
