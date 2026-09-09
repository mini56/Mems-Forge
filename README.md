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
