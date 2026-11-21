# Thin-plate (Kirchhoff) benchmark coefficients, uniform q, nu = 0.3

Notation: Timoshenko writes q a^4/D with a = SHORTER side and ratio b/a >= 1 (Table 35 headers "b/a"). The task text used b = shorter; same numbers, labels swap. Roark uses a/b >= 1 with b = shorter (sigma = beta q b^2/t^2, y = alpha q b^4/(E t^3)).

Method note: Firecrawl credits ran out mid-task (a 595-credit full-book scrape returned only a header). Timoshenko Table 8 itself was NOT read from a book scan; it is reconstructed from papers that tabulate it. Values are as printed in sources.

## A. Simply supported, all edges (Timoshenko & Woinowsky-Krieger Table 8): w = alpha q b^4/D, Mx = beta q b^2, My = beta1 q b^2

Sources
- S1 = Orumu, Int. J. Scientific Advances 3(2), 2022, pp 266-267, Tables 2-3, "Classical" columns. https://www.ijscia.com/wp-content/uploads/2022/04/Volume3-Issue2-Mar-Apr-No.250-263-269.pdf (pdftotext output is layout-scrambled; row alignment partly inferred).
- S2 = Lim, CityU MPhil thesis / Lim 2007 (symplectic), Table 3.1, column "[1]" = Timoshenko and Woinowsky-Krieger 1959. http://lbms03.cityu.edu.hk/theses/c_ftt/mphil-bc-b22687270f.pdf (same data at core.ac.uk/download/pdf/82566558.pdf, not fetched; 403). Only ratios 1.0, 1.2, 1.5, 1.7, 2.0.
- S3 = search excerpts only (not fully read): WVU thesis https://researchrepository.wvu.edu/cgi/viewcontent.cgi?article=5098&context=etd ("1.0 0.00406 0.0479 0.0479 ... 0.065", "1.1 0.00485 0.0554", "2.0 0.01013 0.1017 0.0464", "3.0 0.01223"); Kantorovich-Vlasov paper academia.edu/31782355 ("3 0.01223 0.1189 0.0404; 4 0.01282 0.1235").

| ratio | alpha | beta (Mx) | beta1 (My) | sources |
|---|---|---|---|---|
| 1.0 | 0.00406 | 0.0479 | 0.0479 | S1, S2, S3 agree |
| 1.1 | 0.00485 | 0.0554 | 0.0493 | alpha S3; moments S1 (alignment inferred) |
| 1.2 | 0.00564 | 0.0627 | 0.0501 | S1, S2 (S1 Tab2/4 prints 0.0502 for My; see below) |
| 1.3 | not found | 0.0694 | 0.0503 | S1 only |
| 1.4 | 0.00705 | 0.0755 | 0.0502 | S1 |
| 1.5 | 0.00772 | 0.0812 | 0.0498 | S2 |
| 1.6 | 0.0083 (as printed) | 0.0862 | 0.0492 | S1 |
| 1.7 | 0.00883 | 0.0908 | 0.0486 | S2 |
| 1.8 | 0.00931 | 0.0948 | 0.0479 | S1 |
| 1.9 | not found | 0.0985 | 0.0471 | S1 only |
| 2.0 | 0.01013 (S2,S3); 0.0101 (S1) | 0.1017 | 0.0464 | S1, S2, S3 agree |
| 3 | 0.01223 | 0.1189 | 0.0406 | alpha S3; moments S1 (S3 Kantorovich prints 0.0404) |
| 4 | 0.01282 | 0.1235 | 0.0384 | S1, S3 |
| 5 | not found | 0.1246 | 0.0375 | S1 only |
| inf | not found | 0.125 (S1 row "10") | not found | expected 0.01302 / 0.1250 / 0.0375 NOT verified |

Corner, square SS plate: WVU row for 1.0 ends "0.065", consistent with R = 0.065 q a^2. Mxy ~ 0.0325 q a^2 NOT confirmed by any source read.

Disagreements
- beta1 at 1.2: S2 0.0501 vs S1 Table 2/4 0.0502 (S1 Table 3 gives 0.0503 at what is likely 1.3). Last-digit rounding or S1 row misalignment.
- beta1 at 3: S1 0.0406 vs Kantorovich excerpt 0.0404.
- alpha at 2.0: 0.0101 (S1, 3 sf) vs 0.01013.
- S2 swaps Mx/My labels vs Timoshenko (its column order My then Mx); magnitudes unaffected.

Confidence A: high for ratios 1.0, 1.2, 1.5, 1.7, 2.0 (S1 and S2 independent of each other, both citing Timoshenko). Medium for other moments (single paper, alignment inferred). Low for inf, alpha at 1.3/1.9/5, and the corner twisting moment. Recommend a one-time check of Table 8 in the book scan (e.g. archive.org copy of "Theory of Plates and Shells", Table 8) before formal citation.

## B. Clamped all edges (Timoshenko Table 35, computed by T. H. Evans, J. Appl. Mech. 6, A-7, 1939)

Primary: https://files.engineering.com/files/e40f123f-053e-4747-8ce2-c7f6b302bfa1/Tab_35_from_Timoshenko_Plates.pdf (book page excerpt; "TABLE 35. DEFLECTIONS AND BENDING MOMENTS IN A UNIFORMLY LOADED RECTANGULAR PLATE WITH BUILT-IN EDGES (FIG. 91), nu=0.3"). Table is in terms of qa^4/D and q a^2 with a the short side.
Columns as printed: b/a | (w)x=0,y=0 | (Mx)x=a/2,y=0 | (My)x=0,y=b/2 | (Mx)x=0,y=0 | (My)x=0,y=0.

| b/a | w center (q a^4/D) | (Mx) x=a/2,y=0 | (My) x=0,y=b/2 | (Mx) center | (My) center |
|---|---|---|---|---|---|
| 1.0 | 0.00126 | -0.0513 | -0.0513 | 0.0231 | 0.0231 |
| 1.1 | 0.00150 | -0.0581 | -0.0538 | 0.0264 | 0.0231 |
| 1.2 | 0.00172 | -0.0639 | -0.0554 | 0.0299 | 0.0228 |
| 1.3 | 0.00191 | -0.0687 | -0.0563 | 0.0327 | 0.0222 |
| 1.4 | 0.00207 | -0.0726 | -0.0568 | 0.0349 | 0.0212 |
| 1.5 | 0.00220 | -0.0757 | -0.0570 | 0.0368 | 0.0203 |
| 1.6 | 0.00230 | -0.0780 | -0.0571 | 0.0381 | 0.0193 |
| 1.7 | 0.00238 | -0.0799 | -0.0571 | 0.0392 | 0.0182 |
| 1.8 | 0.00245 | -0.0812 | -0.0571 | 0.0401 | 0.0174 |
| 1.9 | 0.00249 | -0.0822 | -0.0571 | 0.0407 | 0.0165 |
| 2.0 | 0.00254 | -0.0829 | -0.0571 | 0.0412 | 0.0158 |
| inf | 0.00260 | -0.0833 | -0.0571 | 0.0417 | 0.0125 |

Second source (deflection only): Imrak, "An Exact Solution for the Deflection of a Clamped Rectangular Plate under Uniform Load", Applied Math. Sciences 1(42), 2007, https://m-hikari.com/ams/ams-password-2007/ams-password41-44-2007/imrakAMS41-44-2007.pdf. Table (b/a, w(0,0)/(p b^4/D), Evans, Taylor & Govindjee): 1.0 0.00126725 | 0.00126 | 0.00126532; 1.2 0.00172833 | 0.00172 | 0.00172487; 1.6 0.00230399 | 0.00230 | 0.00229997; 1.8 0.00244989 | 0.00245 | 0.00244616; 2.0 0.00253625 | 0.00254 | 0.00253297; inf 0.00260417 | 0.00260 | 0.00260417.

Note: the Timoshenko excerpt's own OCR tail is scrambled; the table above is the clean markdown rendering returned by the scraper. Edge-naming (which edge is "x=a/2") should be checked against book Fig. 91; magnitudes 0.0513 / 0.0829 / 0.0833 match the task's expected values. No disagreement found between sources (Imrak "exact" differs from Evans only in the 3rd-4th sig fig, e.g. 0.00172833 vs 0.00172 (truncated, not rounded)).

Confidence B: high for w (two sources) and for the 1.0/2.0/inf moments; medium-high for intermediate moments (one clean source, Timoshenko itself).

## C. Roark's Formulas for Stress and Strain, Table 11.4 (scans are 7th ed. pp 508-509; sigma = beta q b^2/t^2, y = alpha q b^4/(E t^3), b short side, nu = 0.3)

Sources (Scribd OCR of the same book, so not independent of each other): Case 1a https://www.scribd.com/document/334686201/Tab-11-4-0 ; Case 8a https://www.scribd.com/document/340593062/248898958-Roark-s-Formulas-for-Stress-and-Strain-507-525-11-pdf ("Refs. 7 and 25 and Ref. 21 for nu = 0.3").

Case 1a, all edges simply supported, uniform load. Header a/b: 1.0 1.2 1.4 1.6 1.8 2.0 3.0 4.0 5.0 (OCR shows one extra value; last column is infinity):
- beta : 0.2874 0.3762 0.4530 0.5172 0.5688 0.6102 0.7134 0.7410 0.7476 0.7500
- alpha: 0.0444 0.0616 0.0770 0.0906 0.1017 0.1110 0.1335 0.1400 0.1417 0.1421
- reaction gamma row not captured.

Case 8a, all edges fixed, uniform load. a/b: 1.0 1.2 1.4 1.6 1.8 2.0 inf:
- beta1 (center of long edge): 0.3078 0.3834 0.4356 0.4680 0.4872 0.4974 0.5000
- beta2 (center): 0.1386 0.1794 0.2094 0.2286 0.2406 0.2472 0.2500
- alpha: 0.0138 0.0188 0.0226 0.0251 0.0267 0.0277 0.0284

Cross-check against Timoshenko (arithmetic mine): beta = 6 x moment coeff: 0.2874/6 = 0.0479, 0.6102/6 = 0.1017, 0.7500/6 = 0.1250, 0.3078/6 = 0.0513, 0.4974/6 = 0.0829, 0.5000/6 = 0.0833, 0.1386/6 = 0.0231. Roark alpha = Timoshenko alpha x 12(1-nu^2) = x10.92: 0.00406 -> 0.0443 (Roark 0.0444), 0.00126 -> 0.01376 (0.0138), 0.01302 -> 0.1422 (0.1421), 0.00260 -> 0.0284 (0.0284). Consistent.

Confidence C: high for consistency with Timoshenko; medium for digit-exactness (Scribd OCR). 8th-ed. values not separately checked.
