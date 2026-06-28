# -*- coding: utf-8 -*-
"""
interpolation.py
==================

Motore di interpolazione spaziale per le superfici di falda e LNAPL.

Metodi implementati:
- IDW (Inverse Distance Weighting): default robusto, senza dipendenze
  esterne oltre numpy/scipy, consigliato per reti di monitoraggio con
  pochi punti (tipico dei siti contaminati).
- Ordinary Kriging (tramite la libreria opzionale PyKrige): il modello
  di variogramma (sferico, esponenziale, gaussiano) viene scelto
  automaticamente tramite cross-validation leave-one-out, scegliendo il
  modello con errore di stima piu' basso. Non produce un raster di
  incertezza in questa versione (la varianza di kriging e' comunque
  calcolata internamente e potra' essere esposta in futuro).

NOTA IMPORTANTE sull'affidabilita' del Kriging con poche stazioni di
monitoraggio: con meno di circa 10-15 punti, la stima del variogramma e'
statisticamente debole. Il plugin non impedisce l'uso del Kriging in
questi casi, ma registra un avviso nel Message Log di QGIS.

PyKrige NON e' una dipendenza obbligatoria: se non installata, il metodo
"idw" continua a funzionare normalmente; solo "kriging" risultera' non
disponibile, con un messaggio di errore chiaro che indica come installarla.

Questo modulo non dipende da PyQGIS: usa numpy, scipy e (per il Kriging)
pykrige, quindi e' completamente testabile in isolamento.
"""

import math

import numpy as np

from . import utils

try:
    from pykrige.ok import OrdinaryKriging
    HAS_PYKRIGE = True
except ImportError:
    HAS_PYKRIGE = False


class InterpolationError(Exception):
    """Eccezione dedicata per errori di interpolazione."""
    pass


SUPPORTED_METHODS = ("idw", "kriging")


# ---------------------------------------------------------------------------
# Costruzione griglia
# ---------------------------------------------------------------------------

def compute_grid_extent(points, buffer_ratio=0.15, min_buffer=5.0):
    """Calcola l'estensione (bounding box) della griglia di interpolazione
    a partire dalle coordinate dei punti, aggiungendo un margine.

    :param points: lista di dict con chiavi "x", "y"
    :param buffer_ratio: frazione della dimensione massima del bbox usata
        come margine (default 15%)
    :param min_buffer: margine minimo in unita' di mappa (metri), usato
        quando i punti sono troppo vicini tra loro o il bbox e' degenere
    :return: tupla (xmin, ymin, xmax, ymax)
    """
    if not points:
        raise InterpolationError("Nessun punto disponibile per definire l'estensione griglia.")

    xs = [p["x"] for p in points]
    ys = [p["y"] for p in points]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)

    width = xmax - xmin
    height = ymax - ymin
    span = max(width, height, min_buffer * 2)
    buffer_dist = max(span * buffer_ratio, min_buffer)

    return (xmin - buffer_dist, ymin - buffer_dist, xmax + buffer_dist, ymax + buffer_dist)


def compute_grid_dimensions(extent, cell_size):
    """Calcola il numero di colonne/righe della griglia raster dati
    l'estensione e la dimensione di cella.

    :param extent: tupla (xmin, ymin, xmax, ymax)
    :param cell_size: dimensione cella (stessa unita' delle coordinate, es. m)
    :return: tupla (n_cols, n_rows)
    :raises InterpolationError: se la griglia risulterebbe eccessivamente
        grande (protezione contro cell_size troppo piccolo per errore utente)
    """
    xmin, ymin, xmax, ymax = extent
    if cell_size <= 0:
        raise InterpolationError("La dimensione della cella deve essere positiva.")

    n_cols = max(2, int(math.ceil((xmax - xmin) / cell_size)))
    n_rows = max(2, int(math.ceil((ymax - ymin) / cell_size)))

    max_cells = 4_000_000  # limite di sicurezza (~2000x2000)
    if n_cols * n_rows > max_cells:
        raise InterpolationError(
            "La griglia risultante ({0} x {1} = {2} celle) e' troppo grande. "
            "Aumentare la dimensione di cella.".format(n_cols, n_rows, n_cols * n_rows)
        )

    return n_cols, n_rows


# ---------------------------------------------------------------------------
# IDW (Inverse Distance Weighting)
# ---------------------------------------------------------------------------

def _interpolate_idw(points, value_key, extent, cell_size, power=2.0, search_radius=None,
                      min_neighbors=1, nodata_value=-9999.0):
    """Interpolazione IDW su griglia regolare.

    :param points: lista di dict con "x", "y", value_key
    :param value_key: chiave del valore da interpolare (es. "quota_falda")
    :param extent: tupla (xmin, ymin, xmax, ymax)
    :param cell_size: dimensione cella
    :param power: esponente di potenza IDW (default 2.0, standard)
    :param search_radius: raggio massimo di ricerca (None = nessun limite,
        usa tutti i punti pesati per distanza)
    :param min_neighbors: numero minimo di vicini richiesti entro il
        search_radius per stimare una cella; se non soddisfatto la cella
        diventa nodata (solo se search_radius e' impostato)
    :param nodata_value: valore da assegnare alle celle senza stima
    :return: tupla (array numpy 2D [n_rows, n_cols] con i valori interpolati,
        n_cols, n_rows) — l'array ha origine in alto a sinistra (riga 0 = ymax)
    """
    xs = np.array([p["x"] for p in points], dtype=float)
    ys = np.array([p["y"] for p in points], dtype=float)
    values = np.array([p[value_key] for p in points], dtype=float)

    valid_mask = ~np.isnan(values)
    xs, ys, values = xs[valid_mask], ys[valid_mask], values[valid_mask]

    if len(values) == 0:
        raise InterpolationError(
            "Nessun valore valido disponibile per il campo '{0}'.".format(value_key)
        )

    n_cols, n_rows = compute_grid_dimensions(extent, cell_size)
    xmin, ymin, xmax, ymax = extent

    # Coordinate del centro di ogni cella
    col_centers = xmin + (np.arange(n_cols) + 0.5) * cell_size
    row_centers = ymax - (np.arange(n_rows) + 0.5) * cell_size  # riga 0 = ymax (alto)

    grid_x, grid_y = np.meshgrid(col_centers, row_centers)  # shape (n_rows, n_cols)
    flat_x = grid_x.ravel()
    flat_y = grid_y.ravel()

    result = np.full(flat_x.shape, nodata_value, dtype=float)

    # Calcolo a blocchi per contenere l'uso di memoria su griglie grandi
    block_size = 200_000
    n_total = flat_x.shape[0]

    for start in range(0, n_total, block_size):
        end = min(start + block_size, n_total)
        bx = flat_x[start:end]
        by = flat_y[start:end]

        # distanze tra ogni punto griglia (nel blocco) e ogni punto dato
        dx = bx[:, None] - xs[None, :]
        dy = by[:, None] - ys[None, :]
        dist = np.sqrt(dx * dx + dy * dy)

        # punto griglia esattamente coincidente con un dato misurato:
        # assegna direttamente il valore osservato (evita divisione per zero)
        zero_mask = dist < 1e-9

        if search_radius is not None:
            within = dist <= search_radius
            n_within = within.sum(axis=1)
        else:
            within = np.ones_like(dist, dtype=bool)
            n_within = np.full(bx.shape[0], xs.shape[0])

        with np.errstate(divide="ignore"):
            weights = 1.0 / np.power(dist, power)
        weights[~within] = 0.0

        weights_sum = weights.sum(axis=1)
        weighted_values = (weights * values[None, :]).sum(axis=1)

        with np.errstate(invalid="ignore", divide="ignore"):
            block_result = weighted_values / weights_sum

        # gestisci celle coincidenti con un punto dato
        any_zero = zero_mask.any(axis=1)
        if any_zero.any():
            zero_rows = np.where(any_zero)[0]
            for r in zero_rows:
                first_idx = np.argmax(zero_mask[r])
                block_result[r] = values[first_idx]

        # celle senza vicini sufficienti -> nodata
        insufficient = n_within < max(1, min_neighbors)
        block_result[insufficient] = nodata_value
        block_result[weights_sum == 0] = nodata_value

        result[start:end] = block_result

    grid = result.reshape((n_rows, n_cols))
    return grid, n_cols, n_rows


# ---------------------------------------------------------------------------
# Kriging - stub per estensione futura
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Kriging (Ordinary Kriging, tramite PyKrige)
# ---------------------------------------------------------------------------

# Modelli di variogramma candidati per la selezione automatica.
# "linear" e' incluso come ultima rete di sicurezza: non richiede stima di
# parametri di soglia (sill/range) ed e' quasi sempre calcolabile, anche
# con pochissimi punti.
KRIGING_CANDIDATE_MODELS = ("spherical", "exponential", "gaussian", "linear")

# Soglia sotto la quale il Kriging viene comunque eseguito, ma con un
# avviso esplicito nel Message Log sull'affidabilita' statistica limitata
# della stima del variogramma.
KRIGING_MIN_RECOMMENDED_POINTS = 10


def _cross_validate_variogram_model(xs, ys, values, model):
    """Stima l'errore di previsione leave-one-out per un dato modello di
    variogramma: per ogni punto, lo rimuove temporaneamente, lo predice
    dagli altri punti con Ordinary Kriging, e calcola l'errore rispetto
    al valore osservato.

    :param xs, ys, values: array numpy delle coordinate e valori osservati
    :param model: nome del modello di variogramma PyKrige
        ("spherical", "exponential", "gaussian", "linear", ...)
    :return: RMSE (root mean squared error) delle previsioni leave-one-out,
        oppure None se il modello non e' calcolabile su questi dati
    """
    n = len(values)
    squared_errors = []

    for i in range(n):
        mask = np.ones(n, dtype=bool)
        mask[i] = False
        try:
            ok = OrdinaryKriging(
                xs[mask], ys[mask], values[mask],
                variogram_model=model, verbose=False, enable_plotting=False,
            )
            pred, _ = ok.execute("points", np.array([xs[i]]), np.array([ys[i]]))
            error = float(pred[0]) - float(values[i])
            squared_errors.append(error ** 2)
        except Exception:
            # Modello non calcolabile per questo sottoinsieme di punti
            # (es. variogramma degenere): lo scartiamo dalla selezione.
            return None

    if not squared_errors:
        return None
    return math.sqrt(sum(squared_errors) / len(squared_errors))


def _select_best_variogram_model(xs, ys, values, candidate_models=KRIGING_CANDIDATE_MODELS):
    """Seleziona automaticamente il modello di variogramma con l'errore di
    cross-validation leave-one-out piu' basso, tra i modelli candidati.

    :param xs, ys, values: array numpy delle coordinate e valori osservati
    :param candidate_models: tupla di nomi di modello da provare
    :return: tupla (nome_modello_scelto, rmse) - rmse e' None se nessun
        modello e' risultato calcolabile (in tal caso si ricade su "linear"
        senza cross-validation, come ultima rete di sicurezza)
    :raises InterpolationError: se anche "linear" risulta incalcolabile
    """
    results = {}
    for model in candidate_models:
        rmse = _cross_validate_variogram_model(xs, ys, values, model)
        if rmse is not None:
            results[model] = rmse

    if not results:
        # Nessun modello e' stato calcolabile in cross-validation: proviamo
        # comunque a costruire un OrdinaryKriging "linear" su tutti i punti,
        # senza cross-validation, come ultima rete di sicurezza.
        try:
            OrdinaryKriging(xs, ys, values, variogram_model="linear", verbose=False)
        except Exception as exc:
            raise InterpolationError(
                "Impossibile stimare un variogramma valido per il Kriging "
                "con i punti disponibili: {0}".format(exc)
            )
        return "linear", None

    best_model = min(results, key=results.get)
    return best_model, results[best_model]


def _interpolate_kriging(points, value_key, extent, cell_size, **kwargs):
    """Interpolazione con Ordinary Kriging su griglia regolare, tramite la
    libreria PyKrige. Il modello di variogramma (sferico, esponenziale,
    gaussiano, lineare) viene scelto automaticamente per cross-validation
    leave-one-out, scegliendo quello con errore di stima piu' basso.

    :param points: lista di dict con "x", "y", value_key
    :param value_key: chiave del valore da interpolare (es. "quota_falda")
    :param extent: tupla (xmin, ymin, xmax, ymax)
    :param cell_size: dimensione cella
    :return: tupla (array numpy 2D [n_rows, n_cols], n_cols, n_rows) -
        stessa convenzione di _interpolate_idw(): origine in alto a
        sinistra, riga 0 corrisponde a ymax.
    :raises InterpolationError: se PyKrige non e' installato, o se il
        variogramma non e' calcolabile con i dati disponibili
    """
    if not HAS_PYKRIGE:
        raise InterpolationError(
            "Il metodo Kriging richiede la libreria opzionale 'pykrige', "
            "non installata nell'ambiente Python di QGIS. Installarla con: "
            "pip install pykrige --break-system-packages (eseguito nel "
            "Python usato da QGIS), oppure usare il metodo IDW (default)."
        )

    xs = np.array([p["x"] for p in points], dtype=float)
    ys = np.array([p["y"] for p in points], dtype=float)
    values = np.array([p[value_key] for p in points], dtype=float)

    valid_mask = ~np.isnan(values)
    xs, ys, values = xs[valid_mask], ys[valid_mask], values[valid_mask]

    if len(values) < 3:
        raise InterpolationError(
            "Sono necessari almeno 3 punti con dati validi per il Kriging "
            "(disponibili: {0}). Usare il metodo IDW con cosi' pochi punti."
            .format(len(values))
        )

    if len(values) < KRIGING_MIN_RECOMMENDED_POINTS:
        utils.log(
            "Kriging eseguito con solo {0} punti validi per il campo '{1}': "
            "sotto la soglia consigliata di {2} punti, la stima del "
            "variogramma puo' essere statisticamente poco affidabile. "
            "Valutare l'uso dell'IDW come alternativa piu' robusta in "
            "questo caso.".format(len(values), value_key, KRIGING_MIN_RECOMMENDED_POINTS),
            level="WARNING",
        )

    # Punti duplicati o coincidenti causano variogrammi degeneri: li
    # rimuoviamo mantenendo solo la prima occorrenza per coppia (x, y).
    coords_seen = set()
    keep_mask = np.ones(len(values), dtype=bool)
    for i in range(len(values)):
        key = (round(xs[i], 6), round(ys[i], 6))
        if key in coords_seen:
            keep_mask[i] = False
        else:
            coords_seen.add(key)
    if not keep_mask.all():
        xs, ys, values = xs[keep_mask], ys[keep_mask], values[keep_mask]
        utils.log(
            "Rimossi {0} punti con coordinate duplicate prima del Kriging "
            "(causerebbero un variogramma degenere).".format((~keep_mask).sum()),
            level="WARNING",
        )

    best_model, rmse = _select_best_variogram_model(xs, ys, values)
    if rmse is not None:
        utils.log(
            "Kriging: modello di variogramma selezionato automaticamente "
            "per il campo '{0}': '{1}' (RMSE cross-validation: {2:.4f})."
            .format(value_key, best_model, rmse)
        )
    else:
        utils.log(
            "Kriging: nessun modello e' risultato validabile per "
            "cross-validation con il campo '{0}'; uso il modello 'linear' "
            "senza validazione incrociata.".format(value_key),
            level="WARNING",
        )

    n_cols, n_rows = compute_grid_dimensions(extent, cell_size)
    xmin, ymin, xmax, ymax = extent

    col_centers = xmin + (np.arange(n_cols) + 0.5) * cell_size
    # gridy in ordine DECRESCENTE: PyKrige restituisce zgrid[0,:] in
    # corrispondenza del primo valore di gridy passato, quindi passandolo
    # decrescente (da ymax a ymin) otteniamo la stessa convenzione usata
    # in _interpolate_idw() (riga 0 = ymax, origine in alto a sinistra).
    row_centers_desc = ymax - (np.arange(n_rows) + 0.5) * cell_size

    try:
        ok = OrdinaryKriging(
            xs, ys, values, variogram_model=best_model,
            verbose=False, enable_plotting=False,
        )
        zgrid, _variance = ok.execute("grid", col_centers, row_centers_desc)
    except Exception as exc:
        raise InterpolationError(
            "Errore durante l'esecuzione del Kriging (modello '{0}'): {1}"
            .format(best_model, exc)
        )

    # zgrid e' una MaskedArray: convertiamo le celle mascherate (se
    # presenti) nel valore nodata standard del plugin, per coerenza con
    # il formato prodotto da _interpolate_idw().
    grid = np.array(zgrid.filled(np.nan), dtype=float)
    grid = np.where(np.isnan(grid), -9999.0, grid)

    return grid, n_cols, n_rows


# ---------------------------------------------------------------------------
# Entry point pubblico
# ---------------------------------------------------------------------------

def interpolate_grid(points, value_key, cell_size=1.0, method="idw", extent=None,
                      power=2.0, search_radius=None, min_neighbors=1):
    """Interpola un campo scalare (es. quota falda, spessore LNAPL) su una
    griglia regolare a partire da punti sparsi.

    :param points: lista di dict con "x", "y", value_key
    :param value_key: nome del campo da interpolare
    :param cell_size: dimensione cella della griglia di output (m)
    :param method: "idw" (default) oppure "kriging" (Ordinary Kriging,
        richiede la libreria opzionale pykrige)
    :param extent: tupla (xmin, ymin, xmax, ymax); se None viene calcolata
        automaticamente da compute_grid_extent()
    :param power: esponente IDW
    :param search_radius: raggio massimo di ricerca IDW (None = illimitato)
    :param min_neighbors: numero minimo di vicini per stimare una cella
    :return: dict {
        "grid": np.ndarray 2D (n_rows, n_cols), origine alto-sinistra,
        "extent": (xmin, ymin, xmax, ymax),
        "cell_size": float,
        "n_cols": int, "n_rows": int,
        "nodata_value": float,
    }
    :raises InterpolationError: per metodo non supportato o dati insufficienti
    """
    if len(points) < 2:
        raise InterpolationError(
            "Sono necessari almeno 2 punti con dati validi per interpolare "
            "una superficie. Punti disponibili: {0}.".format(len(points))
        )

    method = (method or "idw").strip().lower()
    if method not in SUPPORTED_METHODS:
        raise InterpolationError(
            "Metodo di interpolazione non riconosciuto: '{0}'. "
            "Metodi supportati: {1}.".format(method, ", ".join(SUPPORTED_METHODS))
        )

    if extent is None:
        extent = compute_grid_extent(points)

    nodata_value = -9999.0

    if method == "idw":
        grid, n_cols, n_rows = _interpolate_idw(
            points, value_key, extent, cell_size,
            power=power, search_radius=search_radius, min_neighbors=min_neighbors,
            nodata_value=nodata_value,
        )
    else:  # "kriging"
        grid, n_cols, n_rows = _interpolate_kriging(points, value_key, extent, cell_size)

    utils.log(
        "Interpolazione '{0}' completata per il campo '{1}': griglia {2}x{3}, "
        "cella {4} m.".format(method, value_key, n_cols, n_rows, cell_size)
    )

    return {
        "grid": grid,
        "extent": extent,
        "cell_size": cell_size,
        "n_cols": n_cols,
        "n_rows": n_rows,
        "nodata_value": nodata_value,
    }
