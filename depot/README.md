# Dépôt PDF MEMS Forge

Déposer ici les fichiers PDF à analyser par MEMS Forge.

Chaque ajout ou modification d'un fichier `*.pdf` ou `*.PDF` dans ce dossier déclenche automatiquement le workflow GitHub Actions `MEMS Forge — PDF intake`.

Le workflow d'entrée ne publie pas directement de données dans la base finale. Il enregistre d'abord l'identité du PDF et lance la chaîne de contrôle prévue pour MEMS Forge.

Règles :
- chaque fichier est identifié par son SHA-256, pas seulement par son nom ;
- deux PDF portant le même nom mais ayant un SHA différent sont considérés comme deux révisions physiques différentes ;
- aucun PDF n'est considéré comme validé uniquement parce que le run technique est vert ;
- toute donnée incertaine devra rester bloquée jusqu'à validation humaine.
