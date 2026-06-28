# -*- coding: utf-8 -*-
"""
hydro_calculations.py
======================

Calcoli idrogeologici di base:
- quota piezometrica della falda (quota testa - soggiacenza)
- unificazione dei dati anagrafici piezometro (da file unico o da layer
  GIS esistente) con i dati di campagna (da Excel/CSV)
- preparazione delle liste di punti pronte per l'interpolazione spaziale

Questo modulo e' indipendente da PyQt5. Dipende da PyQGIS solo nella
funzione che legge un layer GIS esistente (load_piezometers_from_layer);
tutte le altre funzioni sono pure Python e testabili in isolamento.
"""

from . import utils


# Campi richiesti su un layer piezometri GIS esistente (modalita' 2),
# con alias accettati per il riconoscimento automatico dei campi.
#
# NOTA IMPORTANTE: gli alias usati anche nel matching "parziale" (fallback,
# vedi _match_field) devono avere almeno 4 caratteri. Alias troppo corti
# come "id" o "z" generano falsi positivi: "z" e' contenuto in "piezometro"
# (ID_Piezome), "id" e' contenuto in "id_piezometro" insieme al campo "id"
# stesso, rendendo ambigua la scelta. Alias corti sono comunque utili per
# il match ESATTO (campo che si chiama letteralmente "z" o "id"), quindi
# vengono mantenuti ma il matching e' stato reso piu' rigoroso (vedi sotto).
GIS_FIELD_ALIASES = {
    "nome": [
        "nome", "nome_piez", "nomepiez", "id_piezometro", "id_piezome",
        "piezometro", "codice", "name", "id",
    ],
    "quota_testa": [
        "quota_testa", "quota testa", "quota_boccapozzo", "quota_bp",
        "quotabp", "quota_t", "quota_test", "elev", "z",
    ],
    "quota_fondo_filtro": ["quota_fondo", "fondo_filtro", "quota_fondo_filtro"],
}

# Lunghezza minima di un alias per essere ammesso nel matching "parziale"
# (contenuto in un nome campo). Alias piu' corti vengono usati solo per
# il matching esatto, per evitare falsi positivi come "z" dentro "id_piezome".
MIN_ALIAS_LENGTH_FOR_PARTIAL_MATCH = 4


class HydroCalculationError(Exception):
    """Eccezione dedicata per errori nei calcoli idrogeologici."""
    pass


# ---------------------------------------------------------------------------
# Calcolo quota falda
# ---------------------------------------------------------------------------

def compute_water_table_elevation(quota_testa, soggiacenza):
    """Calcola la quota piezometrica della falda.

        quota_falda = quota_testa - soggiacenza

    :param quota_testa: quota testa piezometro (m s.l.m.)
    :param soggiacenza: soggiacenza misurata (m, positiva verso il basso)
    :return: quota falda (m s.l.m.) oppure None se input non validi
    """
    if quota_testa is None or soggiacenza is None:
        return None
    try:
        return float(quota_testa) - float(soggiacenza)
    except (TypeError, ValueError):
        return None


def build_point_dataset(piezometers, campaigns, target_date):
    """Costruisce l'elenco dei punti (con coordinate e valori calcolati)
    per UNA specifica data di campagna, pronto per l'interpolazione.

    :param piezometers: dict {nome: {"x", "y", "quota_testa", "quota_fondo_filtro"}}
        (anagrafica, da file unico o da layer GIS - vedi load_piezometers_from_layer)
    :param campaigns: lista di dict di campagna (output di excel_import,
        chiave "campaigns"), eventualmente con date diverse
    :param target_date: datetime.date della campagna da elaborare
    :return: lista di dict, uno per piezometro con dato disponibile in
        quella data:
            {
                "nome": str,
                "x": float, "y": float,
                "quota_testa": float,
                "soggiacenza": float,
                "quota_falda": float,
                "spessore_lnapl_m": float,
                "lnapl_class": str,
            }
    :raises HydroCalculationError: se non sono presenti dati validi
    """
    points = []
    skipped = []

    for record in campaigns:
        if record["data"] != target_date:
            continue

        nome = record["nome"]
        anagrafica = piezometers.get(nome)
        if anagrafica is None:
            skipped.append(nome)
            continue

        quota_testa = anagrafica.get("quota_testa")
        quota_falda = compute_water_table_elevation(quota_testa, record["soggiacenza"])
        if quota_falda is None:
            skipped.append(nome)
            continue

        points.append({
            "nome": nome,
            "x": anagrafica.get("x"),
            "y": anagrafica.get("y"),
            "quota_testa": quota_testa,
            "soggiacenza": record["soggiacenza"],
            "quota_falda": quota_falda,
            "spessore_lnapl_m": record.get("spessore_lnapl_m", 0.0),
            "lnapl_class": record.get("lnapl_class", "assente"),
        })

    if skipped:
        utils.log(
            "Piezometri esclusi dall'elaborazione per anagrafica mancante o "
            "dati incompleti ({0}): {1}".format(target_date, ", ".join(sorted(set(skipped)))),
            level="WARNING",
        )

    if not points:
        raise HydroCalculationError(
            "Nessun punto valido disponibile per la data {0}. Verificare che "
            "i nomi piezometro nel file di campagna corrispondano a quelli "
            "dell'anagrafica (file unico o layer GIS).".format(target_date)
        )

    return points


def list_available_dates(campaigns):
    """Restituisce la lista ordinata delle date di campagna disponibili.

    :param campaigns: lista di dict di campagna
    :return: lista di datetime.date, ordinata crescente
    """
    return sorted({record["data"] for record in campaigns})


# ---------------------------------------------------------------------------
# Caricamento anagrafica da layer GIS esistente (modalita' 2)
# ---------------------------------------------------------------------------

def _match_field(field_names_normalized, aliases):
    """Trova l'indice di campo che corrisponde a uno degli alias dati.

    Il matching avviene in due fasi:
    1. Match ESATTO (campo == alias): sempre tentato, anche per alias corti
       come "id" o "z".
    2. Match PARZIALE (alias contenuto nel nome campo): tentato solo per
       alias di almeno MIN_ALIAS_LENGTH_FOR_PARTIAL_MATCH caratteri, per
       evitare falsi positivi (es. l'alias "z" che troverebbe corrispondenza
       anche dentro "id_piezome", che contiene la lettera "z" in "piezome").

    :param field_names_normalized: lista di nomi campo normalizzati (lower/strip)
    :param aliases: lista di possibili nomi campo per il campo cercato
    :return: indice campo (int) oppure None se non trovato
    """
    for alias in aliases:
        alias_norm = alias.strip().lower()
        for idx, name in enumerate(field_names_normalized):
            if name == alias_norm:
                return idx
    for alias in aliases:
        alias_norm = alias.strip().lower()
        if len(alias_norm) < MIN_ALIAS_LENGTH_FOR_PARTIAL_MATCH:
            continue
        for idx, name in enumerate(field_names_normalized):
            if alias_norm in name:
                return idx
    return None


def load_piezometers_from_layer(layer):
    """Estrae l'anagrafica piezometri da un layer punti QGIS esistente.

    Il layer deve avere geometria punto e contenere almeno i campi
    equivalenti a "Nome piezometro" e "Quota testa". Le coordinate X/Y
    vengono lette dalla geometria stessa (non da campi separati), nel
    sistema di riferimento del layer.

    :param layer: QgsVectorLayer con geometria punto
    :return: dict {nome: {"x", "y", "quota_testa", "quota_fondo_filtro"}}
    :raises HydroCalculationError: se il layer non e' valido o privo dei
        campi richiesti
    """
    try:
        from qgis.core import QgsWkbTypes
    except ImportError:
        raise HydroCalculationError(
            "Questa funzione richiede l'ambiente PyQGIS (qgis.core)."
        )

    if layer is None:
        raise HydroCalculationError("Nessun layer piezometri selezionato.")

    if QgsWkbTypes.geometryType(layer.wkbType()) != QgsWkbTypes.PointGeometry:
        raise HydroCalculationError(
            "Il layer piezometri deve avere geometria di tipo punto."
        )

    field_names = [f.name() for f in layer.fields()]
    field_names_norm = [n.strip().lower() for n in field_names]

    idx_nome = _match_field(field_names_norm, GIS_FIELD_ALIASES["nome"])
    idx_quota = _match_field(field_names_norm, GIS_FIELD_ALIASES["quota_testa"])
    idx_fondo = _match_field(field_names_norm, GIS_FIELD_ALIASES["quota_fondo_filtro"])

    if idx_nome is None or idx_quota is None:
        raise HydroCalculationError(
            "Il layer piezometri deve contenere un campo 'Nome piezometro' "
            "e un campo 'Quota testa'. Campi trovati: {0}".format(", ".join(field_names))
        )

    nome_field = field_names[idx_nome]
    quota_field = field_names[idx_quota]
    fondo_field = field_names[idx_fondo] if idx_fondo is not None else None

    piezometers = {}
    for feature in layer.getFeatures():
        geom = feature.geometry()
        if geom is None or geom.isEmpty():
            continue
        point = geom.asPoint()

        nome = feature[nome_field]
        if nome in (None, ""):
            continue
        nome = str(nome).strip()

        quota_testa = utils.safe_float(feature[quota_field])
        quota_fondo = utils.safe_float(feature[fondo_field]) if fondo_field else None

        piezometers[nome] = {
            "x": point.x(),
            "y": point.y(),
            "quota_testa": quota_testa,
            "quota_fondo_filtro": quota_fondo,
        }

    if not piezometers:
        raise HydroCalculationError(
            "Nessun piezometro valido trovato nel layer selezionato."
        )

    return piezometers


# ---------------------------------------------------------------------------
# Calcolo differenze tra campagne (Delta)
# ---------------------------------------------------------------------------

def build_delta_dataset(points_date1, points_date2, value_key):
    """Calcola la differenza puntuale di un valore tra due campagne, per i
    soli piezometri presenti in entrambe le date.

    :param points_date1: lista punti (output di build_point_dataset) per la data 1
    :param points_date2: lista punti per la data 2
    :param value_key: chiave del valore da confrontare, es. "quota_falda"
        o "spessore_lnapl_m"
    :return: lista di dict:
        {"nome", "x", "y", "value_1", "value_2", "delta"}
        dove delta = value_2 - value_1 (variazione dalla data 1 alla data 2)
    :raises HydroCalculationError: se non ci sono piezometri in comune
    """
    by_name_1 = {p["nome"]: p for p in points_date1}
    by_name_2 = {p["nome"]: p for p in points_date2}

    common_names = sorted(set(by_name_1.keys()) & set(by_name_2.keys()))
    if not common_names:
        raise HydroCalculationError(
            "Nessun piezometro comune tra le due campagne selezionate: "
            "impossibile calcolare la variazione."
        )

    result = []
    for nome in common_names:
        p1 = by_name_1[nome]
        p2 = by_name_2[nome]
        v1 = p1.get(value_key)
        v2 = p2.get(value_key)
        if v1 is None or v2 is None:
            continue
        result.append({
            "nome": nome,
            "x": p1["x"],
            "y": p1["y"],
            "value_1": v1,
            "value_2": v2,
            "delta": v2 - v1,
        })

    if not result:
        raise HydroCalculationError(
            "Impossibile calcolare la variazione: dati '{0}' mancanti per "
            "tutti i piezometri comuni.".format(value_key)
        )

    return result
