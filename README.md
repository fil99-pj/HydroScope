# HydroScope

Plugin professionale per **QGIS 3.x** per la gestione e l'analisi di dati
di monitoraggio piezometrico in siti contaminati, con particolare
attenzione alla **falda** e al **LNAPL** (Light Non-Aqueous Phase Liquid,
fase separata leggera non acquosa che galleggia sulla falda).

Il plugin **non produce report PDF o Word**: trasforma i dati di
monitoraggio (Excel/CSV + eventuale layer GIS) in **nuovi layer
geospaziali** pronti per l'analisi e la mappatura in QGIS (raster
interpolati, isolinee, punti classificati, layer di variazione tra
campagne).

---

## 1. Requisiti

- **QGIS 3.16 o superiore** (testato concettualmente su QGIS 3.16–3.99; usa solo API PyQGIS stabili)
- Python 3 (quello incluso in QGIS)
- Librerie Python richieste, già incluse nella distribuzione standard di QGIS:
  - `numpy`
  - `scipy`
  - `openpyxl` (per la lettura dei file `.xlsx`)
  - `PyQt5` (incluso in QGIS)
  - `GDAL`/`osgeo` (incluso in QGIS, usato per la scrittura dei raster GeoTIFF)
- Libreria opzionale, richiesta SOLO se si usa il metodo Kriging (l'IDW funziona senza):
  - `pykrige`

Se uno di questi moduli non fosse disponibile nel Python di QGIS (caso raro,
dipende dalla distribuzione), installarlo con l'OSGeo4W Shell (Windows) o il
terminale Python di QGIS (Linux/Mac):

```
pip install openpyxl --break-system-packages
pip install pykrige --break-system-packages
```

---

## 2. Installazione

### Metodo 1 — Installazione da file ZIP (consigliato)

1. In QGIS vai su **Plugin → Gestisci e installa plugin... → Installa da ZIP**
2. Seleziona il file `hydroscope.zip`
3. Clicca **Installa plugin**
4. Una volta installato, attiva il plugin dalla lista (casella di spunta) se non si attiva automaticamente
5. Comparirà l'icona **HydroScope** nella toolbar e nel menu **Plugin**

### Metodo 2 — Installazione manuale

1. Estrai il contenuto del file ZIP nella cartella dei plugin di QGIS:
   - **Windows**: `C:\Users\<utente>\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins\`
   - **Linux**: `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`
   - **macOS**: `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/`
2. Assicurati che la cartella estratta si chiami `hydroscope` e contenga direttamente `metadata.txt`, `__init__.py`, ecc.
3. Riavvia QGIS (o usa **Plugin → Gestisci e installa plugin → Ricarica plugin** se hai installato il plugin "Plugin Reloader")
4. Attiva il plugin da **Plugin → Gestisci e installa plugin → Installati**

---

## 3. Struttura del plugin

```
hydroscope/
├── __init__.py              # entry point richiesto da QGIS (classFactory)
├── metadata.txt              # metadati plugin (nome, versione, dipendenze)
├── mainPlugin.py              # classe principale, orchestrazione, menu/toolbar
├── dialog.py                  # interfaccia utente (PyQt5 puro)
├── excel_import.py            # import e validazione file Excel/CSV
├── hydro_calculations.py       # calcolo quota falda, join anagrafica/campagna
├── lnapl_processing.py         # gestione e classificazione LNAPL
├── interpolation.py            # motore di interpolazione spaziale (IDW)
├── layer_generator.py          # creazione layer raster/vettoriali e simbologia
├── utils.py                    # funzioni di utilita' condivise
├── resources.qrc                # definizione risorse Qt (icone)
├── resources.py                  # modulo risorse (le icone sono caricate da file)
├── icons/
│   └── icon.png                 # icona del plugin
├── example_data/
│   ├── esempio_dimostrativo_sito.xlsx    # dataset demo: 15 piezometri, 3 campagne
│   └── esempio_dimostrativo_sito.csv     # stesso dataset in formato CSV
├── README.md
└── LICENSE
```

---

## 4. Modalita' di utilizzo

Il plugin supporta **due modalita' di input dati**, selezionabili nella
finestra principale:

### Modalita' A — File Excel/CSV unico

Un solo file contiene sia l'anagrafica dei piezometri sia i dati di
campagna. Il plugin crea automaticamente sia il layer punti piezometri
(deducendolo dal file) sia tutte le elaborazioni.

Colonne richieste (nomi flessibili, riconosciuti automaticamente anche
con sinonimi comuni e maiuscole/minuscole):

| Campo | Esempi di intestazione accettati |
|---|---|
| Nome piezometro | `Nome piezometro`, `ID`, `Codice` |
| Coordinata X | `X`, `Coordinata X`, `Est` |
| Coordinata Y | `Y`, `Coordinata Y`, `Nord` |
| Quota testa piezometro | `Quota testa (m s.l.m.)`, `Z` |
| Quota fondo filtro *(opzionale)* | `Quota fondo filtro` |
| Data rilievo | `Data rilievo`, `Data` |
| Soggiacenza | `Soggiacenza (m)` |
| Spessore LNAPL | `Spessore LNAPL (cm)` oppure `(m)` oppure `(mm)` |

**Importante**: in questa modalita' le coordinate X/Y del file non hanno
un sistema di riferimento (CRS) intrinseco. Nella sezione "1. Modalita'
di input" della dialog, specificare esplicitamente il **CRS delle
coordinate X/Y** tramite il selettore dedicato (default proposto: il CRS
del progetto corrente se metrico, altrimenti EPSG:32632 - UTM zona 32N,
adatto per gran parte del Nord Italia). Se il CRS non viene impostato
correttamente, i punti possono essere posizionati in modo errato (es.
coordinate in metri UTM interpretate come gradi WGS84).

Vedi `example_data/esempio_dimostrativo_sito.csv` (o `.xlsx`) per un dataset demo completo: 15 piezometri su un'area di circa 190x125 m, con 3 campagne (gennaio, maggio, settembre 2025) che mostrano un gradiente di falda chiaro e una contaminazione LNAPL localizzata che si espande e poi si attenua nel tempo (utile anche per provare subito il confronto tra campagne). Le coordinate del dataset demo sono in EPSG:32632 (UTM zona 32N): selezionare questo CRS nel selettore prima di importare il file.

### Modalita' B — Layer GIS esistente + file Excel/CSV di campagna

Si utilizza un layer punti piezometri **gia' presente nel progetto QGIS**
(con campi equivalenti a Nome piezometro e Quota testa) e si importa un
file Excel/CSV contenente **solo** i dati variabili nel tempo: Nome
piezometro, Data rilievo, Soggiacenza, Spessore LNAPL.

Per questa modalita' non e' incluso un file di esempio dedicato: e'
sufficiente prendere il file `example_data/esempio_dimostrativo_sito.csv`
(descritto sopra) e rimuovere le colonne X, Y, Quota testa, mantenendo
solo Nome piezometro, Data rilievo, Soggiacenza e Spessore LNAPL — il
plugin riconoscera' automaticamente la modalita' "solo campagna" in
base alle colonne presenti.

In entrambe le modalita', **il file Excel/CSV può contenere più
campagne (più date) nello stesso file**: la lista delle date disponibili
viene popolata automaticamente dopo l'import.

### Il dataset demo incluso

`example_data/esempio_dimostrativo_sito.csv` (e relativo `.xlsx`) simula
un piccolo sito di monitoraggio in aperta campagna nella bassa pianura
novarese (zona di Vespolate, NO), con 15 piezometri (PZ01–PZ15) disposti
lungo un transetto irregolare (non su una griglia regolare, ma seguendo
un andamento a curva con spaziatura non uniforme, più realistico di una
rete di monitoraggio reale) e 3 campagne di monitoraggio (15/01/2025,
15/05/2025, 15/09/2025). Le coordinate sono in UTM32N (EPSG:32632) e
cadono in un'area agricola reale, così la base satellitare di QGIS
mostra un contesto plausibile (campi/risaie) invece di un punto
geografico arbitrario:

- **Falda**: gradiente idraulico naturale e leggibile lungo il
  transetto, soggiacenza ridotta (1.5–3 m), coerente con una falda
  freatica superficiale tipica delle zone risicole della bassa novarese.
- **LNAPL**: una contaminazione localizzata attorno al piezometro
  centrale `PZ08`, che si espande nella campagna di maggio (raggiungendo
  la classe "significativo", >20 cm, su due piezometri) e si attenua
  marcatamente nella campagna di settembre — utile per testare sia le
  mappe singole (tutte le classi di colore sono rappresentate) sia il
  confronto tra campagne.

Per una prima prova rapida: importare il file in modalità "File Excel/CSV
unico", impostare il CRS su EPSG:32632 (UTM zona 32N) nel selettore
dedicato, selezionare la data `2025-05-15` e generare la mappa LNAPL.

---

## 5. Flusso di utilizzo passo-passo

1. Apri il plugin (icona in toolbar o menu **Plugin → HydroScope**)
2. Seleziona la **modalita' di input** (file unico, oppure layer GIS + file campagna). Se scegli la seconda, seleziona anche il layer piezometri dal menu a tendina
3. Clicca **Sfoglia...** e seleziona il file Excel/CSV di campagna, poi clicca **Importa file**
4. Dopo l'import, l'elenco delle **date disponibili** si popola automaticamente
5. Seleziona **una data** (per generare le mappe di una singola campagna) oppure **due date** (per il confronto tra campagne)
6. Verifica/modifica i **parametri di interpolazione** (dimensione cella raster, esponente IDW)
7. Clicca uno dei bottoni di azione:
   - **Genera mappa falda** → crea il raster `Falda_<data>`, le isolinee piezometriche e il layer punti classificati
   - **Genera mappa LNAPL** → crea il raster `LNAPL_<data>`, le isolinee isopach e il layer punti classificati per presenza/assenza LNAPL
   - **Confronta campagne (Δ)** → richiede 2 date selezionate; crea i raster `Delta_Falda_<data1>_vs_<data2>` e `Delta_LNAPL_<data1>_vs_<data2>`, più i relativi layer puntuali di variazione
8. I layer generati vengono aggiunti automaticamente al progetto QGIS corrente, con simbologia già impostata

I file raster/vettoriali generati vengono salvati in una sottocartella
`hydroscope_output/` accanto al file di progetto QGIS (o nella home utente
se il progetto non è ancora stato salvato).

---

## 6. Logica di calcolo ed elaborazioni

- **Quota falda**: `quota_falda = quota_testa − soggiacenza`
- **Classificazione LNAPL** (spessore convertito sempre in metri):
  - `0 cm` → assente
  - `0 – 5 cm` → lieve
  - `5 – 20 cm` → moderato
  - `> 20 cm` → significativo
- **Interpolazione spaziale**: due metodi disponibili.
  - **IDW** (Inverse Distance Weighting), default: robusto per reti di monitoraggio con pochi piezometri, nessuna dipendenza extra. Parametri configurabili: dimensione cella, esponente di potenza. Con piezometri molto vicini tra loro e un esponente alto (es. 2.0) può generare un effetto "a bersaglio" attorno a ogni punto; aumentare la dimensione cella e/o abbassare l'esponente (es. 1.0-1.5) attenua l'effetto.
  - **Kriging** (Ordinary Kriging), tramite la libreria opzionale `pykrige`: il modello di variogramma (sferico, esponenziale, gaussiano, lineare) viene scelto **automaticamente** tramite cross-validation leave-one-out (si stima l'errore di previsione rimuovendo un punto alla volta), scegliendo il modello con errore più basso. Il modello scelto e l'errore stimato vengono riportati nel Message Log di QGIS per trasparenza. Con meno di 10 piezometri il plugin segnala un avviso sulla scarsa affidabilità statistica della stima, ma esegue comunque l'interpolazione. Non produce in questa versione un raster di incertezza (varianza di kriging).
- **Isolinee**: generate con l'algoritmo nativo `gdal:contour` (tramite il framework Processing di QGIS); per il LNAPL i livelli coincidono con le soglie di classificazione (isopach map)
- **Layer di variazione (Δ)**: calcolati cella per cella tra due raster interpolati sulla stessa estensione/risoluzione, con simbologia divergente (rosso = diminuzione, blu = aumento)

---

## 7. Risoluzione problemi comuni

| Problema | Possibile causa / soluzione |
|---|---|
| "Colonne obbligatorie non trovate nel file" | Verificare i nomi delle intestazioni nel file Excel/CSV; consultare la tabella delle colonne accettate al punto 4 |
| "Il layer piezometri deve contenere un campo 'Nome piezometro' e un campo 'Quota testa'" | Nella modalità B, il layer GIS selezionato non ha campi riconoscibili: rinominare i campi o aggiungerli |
| "Nessun piezometro comune tra le due campagne selezionate" | Le due date selezionate non condividono piezometri con dati validi: verificare il file di campagna |
| Il raster non compare con i colori attesi | Verificare che il campo selezionato per l'interpolazione non sia interamente nodata; controllare il Message Log di QGIS (categoria "HydroScope") per warning sulle righe scartate |
| Errore "modulo GDAL non disponibile" | Anomalia della distribuzione QGIS in uso; verificare che QGIS sia installato con il supporto GDAL standard (praticamente sempre presente) |
| I piezometri compaiono in una posizione assurda (es. in mezzo all'oceano, fuori dall'Italia) | In modalita' "file unico", il CRS dichiarato nel selettore non corrisponde a quello reale delle coordinate X/Y nel file. Verificare/correggere il CRS nella sezione "1. Modalita' di input" prima di importare il file |
| Errore "il metodo Kriging richiede la libreria pykrige" | Installare `pykrige` nel Python di QGIS: `pip install pykrige --break-system-packages` (eseguito nel terminale Python di QGIS o nell'OSGeo4W Shell), oppure usare il metodo IDW |
| Il Kriging restituisce una superficie molto "appiattita" rispetto ai dati osservati | Normale con pochi piezometri: il Kriging tende a smussare verso la media quando il variogramma è poco vincolato dai dati. Valutare l'uso dell'IDW come alternativa più fedele ai valori puntuali in reti di monitoraggio piccole |

Per il dettaglio di eventuali righe scartate durante l'import (es. date non
valide, soggiacenza mancante), consultare il **Message Log** di QGIS
(menu **Visualizza → Panelli → Log messaggi**, categoria *"HydroScope"*).

---

## 8. Estendibilita'

Il plugin è progettato in moduli indipendenti e ben separati:

- Per aggiungere il raster di incertezza del Kriging (varianza), che PyKrige calcola già internamente: modificare `_interpolate_kriging()` in `interpolation.py` per restituire anche `_variance`, ed esporla come ulteriore raster in `layer_generator.py`
- Per aggiungere nuovi parametri chimici opzionali dal file Excel: estendere `excel_import.py` (sezione `COLUMN_ALIASES`) e `hydro_calculations.py`
- Per nuove tipologie di layer derivati: aggiungere funzioni dedicate in `layer_generator.py`, seguendo lo stesso pattern delle funzioni esistenti

---

## 9. Licenza e autore

Autore: Filippo Graziano.

Distribuito sotto **licenza MIT** (vedi il file `LICENSE` incluso nel
pacchetto): liberamente utilizzabile, modificabile e ridistribuibile,
anche per scopi commerciali, a condizione di mantenere l'avviso di
copyright originale.

Sviluppato con l'assistenza di un'intelligenza artificiale (Claude, Anthropic).

**Avvertenza d'uso**: questo plugin è uno strumento di prima interpretazione
spaziale ed esplorativa dei dati di monitoraggio piezometrico. I risultati
delle interpolazioni (falda, LNAPL) dipendono sensibilmente dai parametri
scelti (metodo, dimensione cella, esponente IDW o modello di variogramma)
e dalla qualità/densità della rete di monitoraggio. Non costituisce
un'analisi tecnica validata e non sostituisce la valutazione di un
professionista competente in idrogeologia e bonifica dei siti contaminati.
