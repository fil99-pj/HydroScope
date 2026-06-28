# -*- coding: utf-8 -*-
"""
utils.py
========

Funzioni di utilita condivise da tutti i moduli del plugin:
- logging verso il Message Log di QGIS
- validazione dati comuni
- conversioni di unita (cm <-> m)
- helper per nomi layer e date
- gestione errori centralizzata

Nessuna funzione qui dipende da PyQt5: questo modulo e' importabile
anche in contesti di test puro Python (senza QGIS).
"""

import os
import re
import datetime

PLUGIN_NAME = "HydroScope"

# Soglie di classificazione LNAPL (in metri), come da specifica:
#   0 cm           -> assente
#   0  - 5 cm       -> lieve
#   5  - 20 cm      -> moderato
#   > 20 cm         -> significativo
LNAPL_THRESHOLDS_M = {
    "assente": (0.0, 0.0),
    "lieve": (0.0, 0.05),
    "moderato": (0.05, 0.20),
    "significativo": (0.20, float("inf")),
}

LNAPL_CLASS_COLORS = {
    "assente": "#ffffff00",      # trasparente
    "lieve": "#ffff00",          # giallo
    "moderato": "#ff8c00",       # arancione
    "significativo": "#ff0000",  # rosso
}


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log(message, level="INFO"):
    """Scrive un messaggio nel Message Log di QGIS (tab 'HydroScope').

    Se QGIS non e' disponibile (es. test unitari fuori da QGIS) il messaggio
    viene semplicemente stampato su stdout, cosi' il modulo resta testabile
    in isolamento.

    :param message: testo del messaggio
    :param level: "INFO", "WARNING", "CRITICAL", "SUCCESS"
    """
    try:
        from qgis.core import QgsMessageLog, Qgis

        level_map = {
            "INFO": Qgis.Info,
            "WARNING": Qgis.Warning,
            "CRITICAL": Qgis.Critical,
            "SUCCESS": Qgis.Success,
        }
        QgsMessageLog.logMessage(
            str(message), PLUGIN_NAME, level_map.get(level, Qgis.Info)
        )
    except ImportError:
        print("[{0}] {1}: {2}".format(PLUGIN_NAME, level, message))


# ---------------------------------------------------------------------------
# Conversioni di unita
# ---------------------------------------------------------------------------

def to_meters(value, unit):
    """Converte un valore di spessore LNAPL in metri.

    :param value: valore numerico (puo' essere None)
    :param unit: "m", "cm" oppure "mm"
    :return: valore in metri (float) o None se value e' None
    """
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None

    unit = (unit or "m").strip().lower()
    if unit == "mm":
        return value / 1000.0
    if unit == "cm":
        return value / 100.0
    return value


def classify_lnapl_thickness(thickness_m):
    """Classifica lo spessore LNAPL (in metri) secondo le soglie di progetto.

    :param thickness_m: spessore in metri (float) o None
    :return: stringa di classe: "assente", "lieve", "moderato", "significativo"
    """
    if thickness_m is None:
        return "assente"
    try:
        thickness_m = float(thickness_m)
    except (TypeError, ValueError):
        return "assente"

    if thickness_m <= 0.0:
        return "assente"
    elif thickness_m <= 0.05:
        return "lieve"
    elif thickness_m <= 0.20:
        return "moderato"
    else:
        return "significativo"


# ---------------------------------------------------------------------------
# Helper su date e nomi layer
# ---------------------------------------------------------------------------

def parse_date(value):
    """Tenta di interpretare 'value' come data, restituendo un datetime.date.

    Supporta oggetti datetime/date gia' validi (tipico quando arrivano da
    openpyxl) e stringhe in formati comuni (dd/mm/yyyy, yyyy-mm-dd, ecc.).

    :param value: valore proveniente da cella Excel
    :return: datetime.date oppure None se non interpretabile
    """
    if value is None:
        return None

    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value

    text = str(value).strip()
    formats = (
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%d.%m.%Y",
        "%d/%m/%y",
    )
    for fmt in formats:
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def format_date_for_layer_name(date_value):
    """Formatta una data per l'uso in un nome layer, es. 2026-03-15 -> '2026-03-15'.

    Caratteri non validi per i nomi layer/file vengono evitati a monte
    usando solo cifre e trattini.

    :param date_value: datetime.date o stringa
    :return: stringa sicura per nomi layer
    """
    date_obj = parse_date(date_value) if not isinstance(date_value, datetime.date) else date_value
    if date_obj is None:
        return sanitize_layer_name(str(date_value))
    return date_obj.strftime("%Y-%m-%d")


def sanitize_layer_name(name):
    """Rimuove caratteri non sicuri da un nome layer (spazi -> underscore, ecc.).

    :param name: nome proposto
    :return: nome sanificato
    """
    name = str(name).strip()
    name = re.sub(r"[^\w\-\.]+", "_", name)
    return name


def build_layer_name(prefix, date_value, suffix=None):
    """Costruisce un nome layer standard, es. 'Falda_2026-03-15' o
    'Delta_Falda_2026-03-15_vs_2026-06-10'.

    :param prefix: es. "Falda", "LNAPL", "Delta_Falda"
    :param date_value: data campagna (o tupla di due date per i delta)
    :param suffix: suffisso opzionale, es. "isolinee", "punti"
    :return: nome layer completo
    """
    if isinstance(date_value, (tuple, list)) and len(date_value) == 2:
        d1 = format_date_for_layer_name(date_value[0])
        d2 = format_date_for_layer_name(date_value[1])
        base = "{0}_{1}_vs_{2}".format(prefix, d1, d2)
    else:
        base = "{0}_{1}".format(prefix, format_date_for_layer_name(date_value))

    if suffix:
        base = "{0}_{1}".format(base, sanitize_layer_name(suffix))
    return base


# ---------------------------------------------------------------------------
# Validazione
# ---------------------------------------------------------------------------

class ValidationError(Exception):
    """Eccezione dedicata per errori di validazione dati di input."""
    pass


def require_fields(record, fields, context=""):
    """Verifica che tutti i campi richiesti siano presenti e non vuoti.

    :param record: dict con i dati del record
    :param fields: lista di nomi campo obbligatori
    :param context: testo aggiuntivo per messaggi di errore (es. nome piezometro)
    :raises ValidationError: se un campo manca o e' vuoto
    """
    missing = [f for f in fields if record.get(f) in (None, "")]
    if missing:
        raise ValidationError(
            "Campi mancanti{0}: {1}".format(
                " ({0})".format(context) if context else "", ", ".join(missing)
            )
        )


def safe_float(value, default=None):
    """Conversione sicura a float, restituisce 'default' se non convertibile."""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def get_temp_filename(basename, extension="tif"):
    """Genera un percorso file temporaneo univoco per output raster intermedi.

    :param basename: nome base (sara' sanificato)
    :param extension: estensione senza punto, es. "tif"
    :return: percorso assoluto in cartella temporanea di sistema
    """
    import tempfile
    import uuid

    safe_base = sanitize_layer_name(basename)
    unique = uuid.uuid4().hex[:8]
    filename = "{0}_{1}.{2}".format(safe_base, unique, extension)
    return os.path.join(tempfile.gettempdir(), filename)


def interpolation_reminder_text(method, cell_size):
    """Costruisce il promemoria standard mostrato dopo ogni generazione di
    mappa, per ricordare che il risultato e' un'interpolazione sensibile ai
    parametri scelti e non un'analisi tecnica validata.

    :param method: "idw" oppure "kriging"
    :param cell_size: dimensione cella usata (m)
    :return: stringa di promemoria
    """
    method_label = "IDW" if (method or "").strip().lower() == "idw" else "Kriging"
    return (
        "Risultato indicativo (metodo {0}, cella {1} m): variare i parametri "
        "di interpolazione puo' cambiare sensibilmente la mappa. Verificare "
        "sempre con i dati puntuali osservati nei piezometri.".format(
            method_label, cell_size
        )
    )
