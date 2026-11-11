# IA de lecture documentaire de MEMS Forge

## Pourquoi cette IA est différente de l'IA MEMS

- **MEMS Forge** prépare les connaissances à partir des originaux : PDF, pages, valeurs, tableaux, illustrations, contexte, provenance et applicabilité.
- **IA MEMS (ECU MEMS Manager)** répond ultérieurement à des questions en consultant **uniquement les données autorisées** par le contrôle de publication.

Le premier composant ne doit pas produire une réponse de technicien à partir d'un OCR douteux. Il produit un **dossier de propositions avec ses preuves**.

## Chemin de traitement visé

1. Identifier le PDF exact, sa langue et son SHA-256.
2. Examiner la page originale : blanche, texte PDF, raster, tableau, schéma, mixte.
3. Préserver PDF natif, OCR, géométrie et chaque correction automatique.
4. Faire examiner **la même image originale** par un modèle multimodal de vision, **sur l'ordinateur qui héberge MEMS Forge**, sans envoi distant automatique.
5. Exiger une réponse JSON stricte : type de page, extrait source, zone, unité, composant, applicabilité explicitement citée, éléments incertains.
6. Recouper modèle et OCR ; une concordance n'est **pas** une preuve de vérité (mêmes erreurs possibles). Les champs sans source sûre sont exclus de la publication.
7. Reconstituer les relations entre procédures, tableaux et figures. Vérifier les limites d'applicabilité (ECU, véhicule, moteur, génération).
8. Après validations techniques distinctes, fabriquer le pack candidat, vérifier SQLite et sa traçabilité, puis seulement autoriser la distribution.

## Premier composant effectivement codé

`src/mems_forge/local_vision_ai.py` fournit une **connexion réelle à un modèle multimodal local déjà présent**, via l'API Ollama `/api/chat`. Il transmet le rendu d'une page, avec les observations OCR comme simples indices, et impose une structure JSON.

Fonctions et mesures de sécurité :
- l'accès réseau est restreint à `127.0.0.1`, `localhost` ou `::1` en HTTP local ; **pas d'envoi de pages à un service cloud** ;
- aucune dépendance externe ajoutée à MEMS Forge ; le serveur de modèle reste optionnel et **n'est pas installé automatiquement** ;
- pages réellement blanches : pas d'inférence inutile ;
- toute réponse comporte les numéros de page et l'empreinte du PDF ;
- les extraits et rectangles sont validés ; la concordance OCR est signalée séparément ;
- une déclaration « page blanche » contredisant le rendu original est rejetée ;
- `validation_state = unresolved` et `approved_for_publication = false` sont impératifs ;
- aucun ajout automatique dans la base publiée, aucune modification d'ECU MEMS Manager.

**La commande de diagnostic (sur une machine disposant d'Ollama et d'un modèle de vision, et non sur le runner Samsung) :**

```text
mems-forge ai-read-page --pdf "manuel.pdf" --page 92 --model "qwen3-vl:4b" --report "analyse-page-92.json"
```

L'option `--ocr-page` permet d'ajouter le JSON OCR de cette page comme indice. Un modèle tel que `qwen3-vl:4b` constitue une piste à tester sur des documents réellement fournis, **pas un modèle actuellement installé ni approuvé**.

## Contrôles non encore réalisés

- Exécuter un vrai modèle sur le corpus : les tests actuels vérifient le protocole et les protections, pas la précision d'un modèle chargé.
- Comparer les propositions à des références vérifiées indépendamment (valeurs et unités, pinouts, tableaux, procédures, schémas).
- Traiter les pages de continuation et la cohérence entre documents et révisions.
- Déployer le moteur approprié et mesurer RAM, vitesse et taux d'erreurs ; ne pas le faire démarrer par défaut sur le runner GitHub ou un PC Windows peu puissant.
- Connecter les propositions vérifiées à des **entités et provenances** SQLite, avec migration versionnée.
- Corriger séparément le défaut déjà observé du générateur de pack à la page 7.

Les sorties de l'IA sont donc **des candidats utiles**, jamais des faits réputés certains par leur seule génération.
