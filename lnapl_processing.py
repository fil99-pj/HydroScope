# -*- coding: utf-8 -*-
"""
lnapl_processing.py
=====================

Gestione del LNAPL (Light Non-Aqueous Phase Liquid) come fase separata
galleggiante sulla falda.

Questo modulo si occupa di:
- determinare presenza/assenza di LNAPL per ogni punto
- applicare la classificazione automatica per soglie di spessore
- preparare le liste di "livelli" da usare per le isolinee (isopach)
- fornire le definizioni di simbologia (colori/classi) usate da
  layer_generator.py per la resa grafica automatica

La classificazione (vedi anche utils.classify_lnapl_thickness) segue le
soglie di progetto:
    0 cm            -> assente
    0  - 5 cm        -> lieve
    5  - 20 cm       -> moderato
    > 20 cm          -> significativo

Questo modulo non dipende da PyQGIS: e' testabile in isolamento.
"""

from . import utils


# Livelli (in metri) usati per generare le isolinee LNAPL (isopach map).
# Coincidono con le soglie di classificazione, piu' un livello iniziale
# molto basso per intercettare i bordi della pellicola di prodotto libero.
ISOPACH_LEVELS_M = [0.01, 0.05, 0.10, 0.20, 0.30, 0.50]

# Ordine delle classi (per legende e rendering coerente)
LNAPL_CLASS_ORDER = ["assente", "lieve", "moderato", "significativo"]


def classify_points(points):
    """Applica/aggiorna la classificazione LNAPL su una lista di punti.

    I punti gia' prodotti da hydro_calculations.build_point_dataset()
    hanno gia' il campo 'lnapl_class' calcolato in fase di import, ma
    questa funzione e' utile anche per ricalcolare la classificazione
    su dataset derivati (es. dopo un'interpolazione, o per dati esterni).

    :param points: lista di dict con almeno la chiave "spessore_lnapl_m"
    :return: la stessa lista, con il campo "lnapl_class" aggiornato/aggiunto
    """
    for point in points:
        thickness = point.get("spessore_lnapl_m")
        point["lnapl_class"] = utils.classify_lnapl_thickness(thickness)
    return points


def has_lnapl(point):
    """Determina se un punto presenta LNAPL (spessore > 0).

    :param point: dict con chiave "spessore_lnapl_m"
    :return: bool
    """
    thickness = point.get("spessore_lnapl_m")
    return thickness is not None and thickness > 0.0


def summarize_lnapl_status(points):
    """Calcola un riepilogo sintetico della situazione LNAPL per una
    campagna: conteggio per classe e percentuale di piezometri con
    presenza di prodotto libero.

    :param points: lista di dict con "lnapl_class" e "spessore_lnapl_m"
    :return: dict {
        "n_totale": int,
        "n_con_lnapl": int,
        "percentuale_con_lnapl": float,
        "conteggio_per_classe": {classe: int, ...},
        "spessore_massimo_m": float,
        "piezometro_massimo": str o None,
    }
    """
    n_totale = len(points)
    n_con_lnapl = sum(1 for p in points if has_lnapl(p))

    conteggio = {classe: 0 for classe in LNAPL_CLASS_ORDER}
    for p in points:
        classe = p.get("lnapl_class", "assente")
        conteggio[classe] = conteggio.get(classe, 0) + 1

    spessore_massimo = 0.0
    piezometro_massimo = None
    for p in points:
        thickness = p.get("spessore_lnapl_m") or 0.0
        if thickness > spessore_massimo:
            spessore_massimo = thickness
            piezometro_massimo = p.get("nome")

    return {
        "n_totale": n_totale,
        "n_con_lnapl": n_con_lnapl,
        "percentuale_con_lnapl": (100.0 * n_con_lnapl / n_totale) if n_totale else 0.0,
        "conteggio_per_classe": conteggio,
        "spessore_massimo_m": spessore_massimo,
        "piezometro_massimo": piezometro_massimo,
    }


def get_isopach_levels(max_thickness_m=None):
    """Restituisce i livelli (in metri) da usare per generare le isolinee
    LNAPL, eventualmente filtrati in base allo spessore massimo osservato
    (per non generare livelli superiori al massimo dei dati, che
    risulterebbero comunque vuoti).

    :param max_thickness_m: spessore massimo osservato nella campagna
        (opzionale); se fornito, i livelli superiori vengono scartati,
        a meno che non ne resti nessuno (in tal caso si mantiene almeno
        il primo livello per avere una isolinea di riferimento)
    :return: lista di livelli in metri, ordinata crescente
    """
    if max_thickness_m is None:
        return list(ISOPACH_LEVELS_M)

    levels = [lv for lv in ISOPACH_LEVELS_M if lv <= max_thickness_m]
    if not levels:
        levels = [ISOPACH_LEVELS_M[0]]
    return levels
