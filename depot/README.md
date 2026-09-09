# Dépôt PDF MEMS Forge

Déposer ici les fichiers PDF à analyser par MEMS Forge.

Chaque ajout ou modification d'un fichier `*.pdf` ou `*.PDF` dans ce dossier déclenche automatiquement le workflow GitHub Actions `MEMS Forge — PDF intake`.

Les PDF de ce dossier sont gérés avec **Git LFS** afin d'accepter les documents trop volumineux pour l'envoi direct depuis le site GitHub.

Le workflow d'entrée ne publie pas directement de données dans la base finale. Il enregistre d'abord l'identité du PDF et lance la chaîne de contrôle prévue pour MEMS Forge.

## Méthode simple recommandée

Après l'installation initiale de GitHub Desktop et le clonage du dépôt, il suffit de double-cliquer sur le fichier situé à la racine du dépôt :

`DEPOSER_UN_PDF.cmd`

L'outil :
- ouvre une fenêtre pour choisir un PDF ;
- met à jour le dépôt local ;
- copie le PDF dans un dossier de dépôt unique en conservant son nom original ;
- calcule son SHA-256 ;
- utilise Git LFS ;
- crée le commit ;
- pousse le PDF vers `main` ;
- déclenche automatiquement le workflow `MEMS Forge — PDF intake`.

GitHub Desktop n'est donc nécessaire au quotidien que comme installation Git/Git LFS et pour l'authentification GitHub initiale.

## Installation initiale

1. Installer GitHub Desktop.
2. Se connecter au compte GitHub dans GitHub Desktop.
3. Cloner le dépôt `mini56/Mems-Forge` sur le PC.
4. Dans l'Explorateur Windows, ouvrir le dossier local `Mems-Forge`.
5. Pour chaque nouveau PDF, double-cliquer sur `DEPOSER_UN_PDF.cmd`.

Pour obtenir un run distinct par PDF, envoyer les PDF un par un.

Règles :
- chaque fichier est identifié par son SHA-256, pas seulement par son nom ;
- deux PDF portant le même nom mais ayant un SHA différent sont considérés comme deux révisions physiques différentes ;
- chaque dépôt est placé dans un dossier unique afin de ne jamais écraser silencieusement une révision précédente ;
- aucun PDF n'est considéré comme validé uniquement parce que le run technique est vert ;
- toute donnée incertaine devra rester bloquée jusqu'à validation humaine.
