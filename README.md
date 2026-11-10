# MEMS Forge

MEMS Forge construit, contrôle et publie la base documentaire destinée à MEMS Manager V2.

## Chaîne de référence

**source originale identifiée → lecture du PDF → extraction brute → reconstruction de la structure → interprétation structurée → provenance → validation → pack → base publiée**

## Principes non négociables

- La fidélité au PDF source prime sur la quantité de données extraites.
- MEMS Forge doit lire la page comme un document : structure visuelle + objets PDF natifs.
- Aucune donnée structurée ne peut faire disparaître sa source brute.
- Toute donnée publiée doit être traçable jusqu'au PDF, à sa révision binaire, à sa page et à sa zone source.
- Une ambiguïté n'est jamais corrigée silencieusement : elle devient une donnée à valider et bloque le pack.
- Une donnée technique possède un domaine d'applicabilité ; elle ne doit jamais être généralisée au-delà de ce que démontre la source.
- Plusieurs PDF peuvent confirmer la même donnée ; leurs provenances restent toutes conservées.
- Deux valeurs différentes ne sont pas considérées comme contradictoires avant comparaison de leur applicabilité (véhicule, moteur, ECU, année, marché, configuration, etc.).
- Le socle de la base est permanent et extensible ; les données métier futures sont ajoutées par migrations versionnées.
- MEMS Manager V2 consomme uniquement une base déjà validée et publiée par MEMS Forge.
- Le Database Tester interroge directement la base, sans IA, et n'est pas intégré au programme final MEMS Manager V2.

## État initial

Le dépôt démarre volontairement sans moteur PDF spécifique. Le premier socle technique est construit indépendamment du corpus de test. Un PDF de référence sera nécessaire avant d'implémenter et de valider le lecteur/préleveur PDF.


## Audit du vrai Setup et testeur de base (sans IA)

MEMS Forge peut contrôler les **47 PDF RAVE originaux** par taille, empreinte Git,
SHA-256 et pagination. La liste figée de référence est dans
data/rave_47_originals.json. Elle pointe vers rave/ dans la branche main du dépôt
mini56/ECU-Mems-Manager-Session. Les autres PDF fournis doivent être inventoriés
séparément : ils ne sont jamais présumés présents.

Le contrôle lit le *Setup réellement installé*, et non une simple copie de ses
sources. Il vérifie l'intégrité SQLite et ses tables, la présence des fichiers
de référence et des enrichissements, les 433 images référencées par leur
manifeste et leurs empreintes, et la correspondance des noms des PDF avec les
documents effectivement indexés dans MEMSLibrary Pack001.

Commandes utilisables sur Windows avec Python et MEMS Forge installés :

    mems-forge audit-setup --source-repo C:\sources\ECU-Mems-Manager-Session --install-root "C:\Program Files\ECU MEMS Manager SESSION" --baseline data\rave_47_originals.json --report-dir out\package-audit

    mems-forge tester --db "C:\Program Files\ECU MEMS Manager SESSION\MEMSLibrary_Pack_001\knowledge.sqlite" --query lambda

Les rapports audit_integralite.json et audit_integralite.md distinguent les
fichiers manquants, les bases défectueuses, les originaux sans correspondance
dans le pack et la couverture documentaire qui reste à vérifier.

BLOCKED = anomalie réelle ou entrée requise manquante. REVIEW_REQUIRED =
inventaire techniquement vérifié, mais exhaustivité sémantique, tableaux
et figures non encore certifiés. Aucun contrôle technique n'est présenté
comme preuve que tout le contenu des PDF a été extrait et interprété.

Le workflow GitHub MEMS Forge - audit corpus et Setup installé exécute les
tests, puis sur demande installe le Setup 136 Windows réel et publie un rapport
téléchargeable. Le contrôle ne modifie ni les bases installées ni l'interface
d'ECU MEMS Manager.
