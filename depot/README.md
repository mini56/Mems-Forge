# Dépôt PDF MEMS Forge

Déposer ici les fichiers PDF à analyser par MEMS Forge.

Chaque ajout ou modification d'un fichier `*.pdf` ou `*.PDF` dans ce dossier déclenche automatiquement le workflow GitHub Actions `MEMS Forge — PDF intake`.

Les PDF de ce dossier sont gérés avec **Git LFS** afin d'accepter les documents trop volumineux pour l'envoi direct depuis le site GitHub.

Le workflow d'entrée ne publie pas directement de données dans la base finale. Il enregistre d'abord l'identité du PDF et lance la chaîne de contrôle prévue pour MEMS Forge.

## Dépôt recommandé avec GitHub Desktop

1. Installer et ouvrir GitHub Desktop.
2. Cloner le dépôt `mini56/Mems-Forge` sur le PC.
3. Ouvrir dans l'Explorateur Windows le dossier local du dépôt.
4. Copier le PDF dans le sous-dossier `depot`.
5. Revenir dans GitHub Desktop : le PDF doit apparaître dans les changements.
6. Saisir un message de commit, par exemple `Add test PDF`.
7. Cliquer sur `Commit to main`.
8. Cliquer sur `Push origin`.
9. Le push déclenche automatiquement le workflow `MEMS Forge — PDF intake`.

Pour obtenir un run distinct par PDF, déposer et pousser les PDF un par un.

Règles :
- chaque fichier est identifié par son SHA-256, pas seulement par son nom ;
- deux PDF portant le même nom mais ayant un SHA différent sont considérés comme deux révisions physiques différentes ;
- aucun PDF n'est considéré comme validé uniquement parce que le run technique est vert ;
- toute donnée incertaine devra rester bloquée jusqu'à validation humaine.
