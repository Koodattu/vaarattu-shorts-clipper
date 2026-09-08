# Review volume and pacing audit — 8 September 2026

Run `002b6046f037478db0c4e4d504af535a`, VIsHkvljtOU, conversation-v9. Read-only database/checkpoint inspection; no production artifacts or reviews modified. This is a new completed run, separate from the cancelled v8 run that proposed 159 clips.

## Findings

- 69 discovery proposals, 66 verified candidates, 63 ready, 3 held for alignment checks.
- Human reviews: 10 approved, 53 not approved, 3 unreviewed (held).
- Verifier: 50 accept, 10 needs_context, 6 reject. All 10 approvals were accept; 39 rejects by the creator were also model accept.
- Original accept + standalone/substance/fidelity >=3 gates retain 25 (7 approved, 17 not approved, 1 held); they lose 3 known good clips.
- Simple highest-total-score ten in the saved delivery order contain 4 approvals. Reweighting the same coarse scores is not evidence of better selection.
- New basic gates (accept, substance >=3, standalone/fidelity >=2) retain 43 including all 10 approvals, 32 not approved and 1 held. These are candidate admission gates, not the final ten.
- A separate comparative pass ranks all admitted candidates and recommends review/defer from original excerpt text and model notes. The application deduplicates substantially overlapping moments and takes at most ten; it does not request ten from the model. This new model judgment has not been run on production data.
- High caption-warning counts or long pauses are not automatic content rejection reasons. Pause defects can be repaired. Publication decisions are not universal negative training labels.

## Pacing

The old detector required >=2 seconds below -50 dB plus a transcript gap. The neighbour and CPU clips had no qualifying acoustic silence. The anime clip was from an older run with trimming disabled. Per the creator's correction, v2 uses transcript timestamps alone: gaps >=1.5 seconds, 300 ms retained on each side, no cuts across recorded timing disputes or word spans. Output shorter than 3 seconds is not allowed. Speech entirely missed by ASR remains a limitation.

| Clip | Previous output | Planned output | Cuts |
|---|---:|---:|---:|
| Kun anime keksii mangalle oman lopun | 54.87s | 31.98s | 2 |
| Stormreaveriltä tulee lootin perässä käyviä ongelmapelaajia | 67.37s | 61.68s | 4 |
| Kun neliydinprossusta sai kuusiytimisen | 64.91s | 47.00s | 6 |
| Naapurin mystinen katoaminen | 51.77s | 22.92s | 5 |

These are offline edit plans, not new renders. All canonical word IDs were retained and captions retimed successfully. Existing media/reviews remain untouched.

## Full review ledger

Scores are standalone / opening / substance / payoff / fidelity. All model reasons and the complete proposed pacing maps are in [the evidence JSON](2026-09-08-review-calibration-evidence.json).

| Clip | Creator | Model | Scores | Human reason |
|---|---|---|---|---|
| Diablo 4:n Warlock ei ole koskaan ollut hyvä | not_approved | accept | 3/4/4/4/3 |  |
| “Ei sulle naureta… tai siis nauretaan sulle” | approved | accept | 3/3/3/4/4 |  |
| Suuri johtaja antaa viikon lomaa | approved | accept | 3/3/3/4/4 |  |
| Stormreaveriltä tulee lootin perässä käyviä ongelmapelaajia | approved | accept | 3/3/4/3/4 |  |
| Yksi sadasta, juuri oikea kypärä | not_approved | accept | 3/3/4/4/3 |  |
| Kun vino hylly syyttää seinää | not_approved | accept | 3/3/4/4/3 |  |
| Täydellinen diili: RTX 3060 takaisin myyntiin | approved | accept | 3/3/3/4/3 |  |
| Kahden viikon toipumisaika pelistä | unreviewed | accept | 3/3/3/4/3 |  |
| Kun neliydinprossusta sai kuusiytimisen | not_approved | accept | 3/3/4/3/3 |  |
| Todennäköisesti sun vika | not_approved | accept | 3/3/3/4/3 |  |
| Witcher 3 oli PC:llä jo valmiiksi next-gen | not_approved | accept | 3/3/3/3/4 |  |
| Fyrakki ja positiivinen ilmapiiri | not_approved | accept | 3/3/3/4/3 |  |
| “Vihdoinkin seuraavassa raidissa” | not_approved | accept | 3/3/3/3/4 |  |
| Luota tiimiin — tai kanna syy | not_approved | accept | 3/3/3/4/3 |  |
| Osanotot, Janski on kohta 50 | not_approved | accept | 3/3/3/3/4 |  |
| “Seasonin paras dungani” muuttuikin aika kurjaksi | not_approved | accept | 3/2/3/4/3 |  |
| ”Puhutko suomea?” | not_approved | accept | 3/3/3/3/3 |  |
| Kun goldia onkin liikaa | not_approved | accept | 3/3/3/3/3 | maybe with more context |
| Kun hahmon sukunimi ei ole Storm Reaver | approved | accept | 3/3/3/3/3 |  |
| Kun loot menee ihmisten edelle | approved | accept | 3/2/3/3/4 |  |
| Naapurin mystinen katoaminen | approved | accept | 3/3/3/4/2 |  |
| Uusi Warlock, vanhat ongelmat | not_approved | accept | 3/2/3/3/4 |  |
| Ionin keittiön paskalintu | approved | accept | 2/2/3/4/4 |  |
| Kun tekoälymalli ei suostu koodaamaan | not_approved | accept | 3/3/3/3/3 |  |
| “Mä en ainakaan ajattele” | not_approved | accept | 3/2/3/4/3 |  |
| Mushoku Tensein hyvä maailma jättää oudon fiiliksen | approved | accept | 3/2/3/3/4 |  |
| Heinäkuu on epävirallinen lomakuukausi | not_approved | accept | 3/3/2/2/4 |  |
| Miksi pienet tekoälymallit eivät vielä riitä? | not_approved | accept | 3/3/3/3/2 |  |
| Dragonflight-traumat | not_approved | accept | 3/3/3/2/3 |  |
| Kuinka monta guildia tämä bossi vielä tappaa? | not_approved | accept | 2/3/3/3/3 |  |
| Treasure Breach oli kultakaivos | not_approved | accept | 2/2/3/3/4 |  |
| “Käytä kesäloma siihen” | not_approved | accept | 2/2/3/3/4 | maybe with more earlier context |
| Kun rasistiselle vitsille odotetaan aplodeja | not_approved | needs_context | 2/1/3/3/4 |  |
| “Mulla on semmonen tyyni valtameri” | approved | accept | 2/3/3/3/2 |  |
| Mitä itemin tähdet oikeasti tarkoittavat? | not_approved | accept | 3/2/3/3/2 |  |
| ”Firma ei tehnyt muuta kuin niputti modit yhteen” | not_approved | needs_context | 2/2/3/3/3 |  |
| Espanjan-loma olikin guildireissu | not_approved | accept | 2/3/3/3/2 |  |
| Lil Häiske olisi ylpeä | not_approved | accept | 2/2/2/3/4 |  |
| Miten LHR rajoitti näytönohjainten louhintaa | not_approved | accept | 2/2/3/3/3 |  |
| Jaettua RAM-muistia, mutta vähän dedikoitua muistia | not_approved | accept | 2/2/3/3/3 |  |
| Kenen puolella HR oikeasti on? | not_approved | accept | 3/3/3/2/2 |  |
| Miljoona pelituntia ja kaikki meni vituiksi | not_approved | needs_context | 2/3/3/3/2 |  |
| Kun vaikeampi mekaniikka ei ollutkaan pahin | not_approved | needs_context | 2/2/3/3/2 |  |
| Älä paina pommia yksittäin | not_approved | accept | 2/2/3/3/2 |  |
| Bossi ei pysty enää tekemään mitään | not_approved | accept | 2/2/3/3/2 |  |
| Aina valmiina lähtemään | not_approved | accept | 2/2/2/3/3 |  |
| Bugittava dispel ei mene cooldownille | not_approved | accept | 2/2/3/2/3 |  |
| Laillista uhkapelaamista | not_approved | needs_context | 2/3/2/3/2 |  |
| Diabloa katsoessa tulee vain päänsärky | not_approved | needs_context | 2/3/3/2/2 |  |
| Teleportit korjaantuivat vasta toisella avauksella | not_approved | accept | 2/2/3/3/2 |  |
| Balls against my face | not_approved | accept | 2/3/2/3/2 |  |
| Räppäri Haaland Discord-DM:ssä | not_approved | accept | 2/3/2/2/2 |  |
| “Jos en lähde Alter Timella, en tiedä mitä teen” | not_approved | accept | 2/2/2/3/2 |  |
| Mummot osasivat oikeasti kokata | not_approved | accept | 2/2/3/2/2 |  |
| Nyt meni 40–50 sekuntia nopeammin | unreviewed | needs_context | 2/3/3/1/2 |  |
| Vain jättiveto voi enää pelastaa tilanteen | not_approved | needs_context | 1/2/2/2/3 |  |
| Kun ilmainen Assy-lippu on jo luvattu kaikille | not_approved | accept | 2/2/2/1/3 |  |
| Jatkuuko raid samasta kohdasta? | not_approved | accept | 2/2/3/1/2 |  |
| Tuhat tuntia Bad Saste of Exileä | not_approved | reject | 1/3/1/2/2 |  |
| Magen ympärille rakennettu metatiimi | unreviewed | reject | 2/2/2/1/2 |  |
| CP:ien manifestointi | not_approved | reject | 1/2/1/1/4 |  |
| Kielimalli juttuseuraksi? | not_approved | needs_context | 2/2/2/1/2 | nothing clip worthy |
| "Painan niin paljon kun tästä koneesta lähtee" | not_approved | needs_context | 1/2/2/2/1 | nothing clip worthy |
| Mage’s Season ja Arcane palaavat | not_approved | reject | 1/1/2/1/2 | nothing clip worthy |
| Hahmo tuntuu heikommalta kuin viimeksi | not_approved | reject | 1/2/1/0/3 | nothing clip worthy |
| Epäselvä vatupassivitsi | not_approved | reject | 1/1/1/1/1 |  |

## Implementation verification

236 Python tests passed; one pre-existing Codex admission fixture fails because it supplies an unnamed layout. Six UI tests and lint passed. A synthetic FFmpeg multi-cut test produced synchronized 7.2-second audio/video and 216 frames at 30 fps from a 12-second source. No production inference, VOD rerender, app restart, saved review change or source checkpoint change was performed. The new comparative ranking has not yet been measured against creator reviews.
