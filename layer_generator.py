# -*- coding: utf-8 -*-
"""
layer_generator.py
====================

Modulo responsabile della creazione dei layer GIS finali in QGIS, a
partire dai dati elaborati da interpolation.py, hydro_calculations.py e
lnapl_processing.py.

Questo e' l'UNICO modulo che produce output visibili in QGIS (nessun
report PDF/Word viene generato, in linea con i requisiti del plugin).

Layer prodotti:
- Raster falda interpolata ("Falda_<data>")
- Raster LNAPL interpolato ("LNAPL_<data>")
- Vettoriale isolinee piezometriche (curve isopiezometriche, opzionale)
- Vettoriale isolinee LNAPL (isopach map)
- Vettoriale punti piezometri classificati (presenza/assenza LNAPL)
- Raster e vettoriale di variazione (Delta) tra due campagne

Questo modulo dipende da PyQGIS (qgis.core) e da osgeo/GDAL (tramite
QGIS) per la scrittura raster. Non e' eseguibile fuori da un ambiente
QGIS; viene quindi richiamato solo da mainPlugin.py.
"""

import os

from qgis.core import (
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
    QgsField,
    QgsFields,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsWkbTypes,
    QgsCoordinateReferenceSystem,
    QgsRasterShader,
    QgsColorRampShader,
    QgsSingleBandPseudoColorRenderer,
    QgsCategorizedSymbolRenderer,
    QgsRendererCategory,
    QgsMarkerSymbol,
    QgsLineSymbol,
    QgsVectorFileWriter,
    edit,
)
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtGui import QColor

try:
    from osgeo import gdal, osr
    HAS_GDAL = True
except ImportError:
    HAS_GDAL = False

import processing

from . import utils
from . import lnapl_processing


class LayerGenerationError(Exception):
    """Eccezione dedicata per errori nella creazione dei layer GIS."""
    pass


# ---------------------------------------------------------------------------
# Scrittura raster (numpy array -> GeoTIFF -> QgsRasterLayer)
# ---------------------------------------------------------------------------

def _write_geotiff(grid, extent, cell_size, output_path, nodata_value, crs_wkt):
    """Scrive un array numpy 2D come file GeoTIFF georeferenziato.

    :param grid: array numpy 2D (n_rows, n_cols), origine alto-sinistra
    :param extent: tupla (xmin, ymin, xmax, ymax)
    :param cell_size: dimensione cella
    :param output_path: percorso file .tif di output
    :param nodata_value: valore nodata
    :param crs_wkt: WKT del sistema di riferimento (preso dal layer piezometri)
    :raises LayerGenerationError: se GDAL non e' disponibile o la scrittura fallisce
    """
    if not HAS_GDAL:
        raise LayerGenerationError(
            "Il modulo GDAL (osgeo) non e' disponibile nell'ambiente Python "
            "di QGIS: impossibile scrivere il raster di output."
        )

    n_rows, n_cols = grid.shape
    xmin, ymin, xmax, ymax = extent

    driver = gdal.GetDriverByName("GTiff")
    dataset = driver.Create(output_path, n_cols, n_rows, 1, gdal.GDT_Float32)
    if dataset is None:
        raise LayerGenerationError(
            "Impossibile creare il file raster: {0}".format(output_path)
        )

    # GeoTransform: (origine_x, px_width, 0, origine_y, 0, -px_height)
    # origine_y e' l'angolo in alto a sinistra (ymax), come la riga 0 della griglia
    geotransform = (xmin, cell_size, 0.0, ymax, 0.0, -cell_size)
    dataset.SetGeoTransform(geotransform)

    if crs_wkt:
        srs = osr.SpatialReference()
        srs.ImportFromWkt(crs_wkt)
        dataset.SetProjection(srs.ExportToWkt())

    band = dataset.GetRasterBand(1)
    band.SetNoDataValue(float(nodata_value))
    band.WriteArray(grid.astype("float32"))
    band.FlushCache()
    dataset.FlushCache()
    dataset = None  # chiude il dataset e scrive su disco

    return output_path


def _load_raster_layer(path, layer_name):
    """Carica un file raster come QgsRasterLayer.

    :param path: percorso file raster
    :param layer_name: nome da assegnare al layer in QGIS
    :return: QgsRasterLayer valido
    :raises LayerGenerationError: se il layer non risulta valido
    """
    layer = QgsRasterLayer(path, layer_name)
    if not layer.isValid():
        raise LayerGenerationError(
            "Il layer raster generato non e' valido: {0}".format(path)
        )
    return layer


# ---------------------------------------------------------------------------
# Simbologia raster
# ---------------------------------------------------------------------------

def apply_water_table_symbology(raster_layer, min_value, max_value):
    """Applica una simbologia a gradiente continuo (pseudo-color) alla
    superficie della falda, dal blu scuro (quote basse) al blu chiaro/
    azzurro (quote alte) - palette intuitiva per una superficie piezometrica.

    :param raster_layer: QgsRasterLayer della falda
    :param min_value: valore minimo della superficie (per la scala colori)
    :param max_value: valore massimo della superficie
    """
    shader = QgsRasterShader()
    color_ramp = QgsColorRampShader()
    color_ramp.setColorRampType(QgsColorRampShader.Interpolated)

    if max_value <= min_value:
        max_value = min_value + 1.0  # evita range degenere

    stops = [0.0, 0.25, 0.5, 0.75, 1.0]
    colors = [
        QColor(33, 50, 130),    # blu scuro - quote piu' basse
        QColor(49, 104, 184),
        QColor(95, 160, 212),
        QColor(170, 215, 235),
        QColor(230, 245, 250),  # quasi bianco - quote piu' alte
    ]

    color_ramp_items = []
    for stop, color in zip(stops, colors):
        value = min_value + stop * (max_value - min_value)
        color_ramp_items.append(QgsColorRampShader.ColorRampItem(value, color))

    color_ramp.setColorRampItemList(color_ramp_items)
    shader.setRasterShaderFunction(color_ramp)

    renderer = QgsSingleBandPseudoColorRenderer(raster_layer.dataProvider(), 1, shader)
    raster_layer.setRenderer(renderer)
    raster_layer.triggerRepaint()


def apply_lnapl_symbology(raster_layer):
    """Applica la simbologia a classi per il raster LNAPL, come da
    specifica:
        0            -> assente (trasparente/bianco)
        0  - 5 cm     -> giallo
        5  - 20 cm    -> arancione
        > 20 cm       -> rosso

    :param raster_layer: QgsRasterLayer dello spessore LNAPL (valori in metri)
    """
    shader = QgsRasterShader()
    color_ramp = QgsColorRampShader()
    color_ramp.setColorRampType(QgsColorRampShader.Interpolated)

    # Valori soglia in METRI (i raster LNAPL sono sempre prodotti in metri,
    # la conversione cm->m avviene a monte in excel_import/utils)
    color_ramp_items = [
        QgsColorRampShader.ColorRampItem(0.0, QColor(255, 255, 255, 0)),      # assente: trasparente
        QgsColorRampShader.ColorRampItem(0.0001, QColor(255, 255, 0, 200)),    # soglia lieve: giallo
        QgsColorRampShader.ColorRampItem(0.05, QColor(255, 165, 0, 220)),      # soglia moderato: arancione
        QgsColorRampShader.ColorRampItem(0.20, QColor(255, 0, 0, 255)),        # soglia significativo: rosso
        QgsColorRampShader.ColorRampItem(0.50, QColor(139, 0, 0, 255)),        # estremo: rosso scuro
    ]
    color_ramp.setColorRampItemList(color_ramp_items)
    shader.setRasterShaderFunction(color_ramp)

    renderer = QgsSingleBandPseudoColorRenderer(raster_layer.dataProvider(), 1, shader)
    raster_layer.setRenderer(renderer)
    raster_layer.triggerRepaint()


def apply_delta_symbology(raster_layer, min_value, max_value):
    """Applica una simbologia divergente (rosso-bianco-blu) per i raster
    di variazione (Delta) tra due campagne: valori negativi (diminuzione)
    in una tonalita', positivi (aumento) nell'altra, zero al centro.

    :param raster_layer: QgsRasterLayer della differenza
    :param min_value: valore minimo (tipicamente negativo)
    :param max_value: valore massimo (tipicamente positivo)
    """
    shader = QgsRasterShader()
    color_ramp = QgsColorRampShader()
    color_ramp.setColorRampType(QgsColorRampShader.Interpolated)

    abs_extreme = max(abs(min_value), abs(max_value), 0.01)

    color_ramp_items = [
        QgsColorRampShader.ColorRampItem(-abs_extreme, QColor(178, 24, 43)),    # forte diminuzione: rosso
        QgsColorRampShader.ColorRampItem(-abs_extreme / 2, QColor(244, 165, 130)),
        QgsColorRampShader.ColorRampItem(0.0, QColor(247, 247, 247)),          # nessuna variazione: bianco
        QgsColorRampShader.ColorRampItem(abs_extreme / 2, QColor(146, 197, 222)),
        QgsColorRampShader.ColorRampItem(abs_extreme, QColor(33, 102, 172)),    # forte aumento: blu
    ]
    color_ramp.setColorRampItemList(color_ramp_items)
    shader.setRasterShaderFunction(color_ramp)

    renderer = QgsSingleBandPseudoColorRenderer(raster_layer.dataProvider(), 1, shader)
    raster_layer.setRenderer(renderer)
    raster_layer.triggerRepaint()


# ---------------------------------------------------------------------------
# Creazione layer raster principali (falda / LNAPL / delta)
# ---------------------------------------------------------------------------

def create_raster_layer(interpolation_result, output_dir, layer_name, crs_wkt,
                          symbology="water_table", add_to_project=True):
    """Crea un layer raster QGIS a partire dal risultato di interpolation.interpolate_grid().

    :param interpolation_result: dict restituito da interpolate_grid()
    :param output_dir: cartella dove scrivere il file .tif
    :param layer_name: nome del layer (e base del nome file)
    :param crs_wkt: WKT del sistema di riferimento da assegnare al raster
    :param symbology: "water_table", "lnapl", o "delta" - determina la
        simbologia automatica applicata
    :param add_to_project: se True, aggiunge il layer al progetto QGIS corrente
    :return: QgsRasterLayer creato
    :raises LayerGenerationError: per errori di scrittura o validita' layer
    """
    os.makedirs(output_dir, exist_ok=True)
    safe_name = utils.sanitize_layer_name(layer_name)
    output_path = os.path.join(output_dir, "{0}.tif".format(safe_name))

    grid = interpolation_result["grid"]
    extent = interpolation_result["extent"]
    cell_size = interpolation_result["cell_size"]
    nodata_value = interpolation_result["nodata_value"]

    _write_geotiff(grid, extent, cell_size, output_path, nodata_value, crs_wkt)
    raster_layer = _load_raster_layer(output_path, layer_name)

    valid_values = grid[grid != nodata_value]
    if valid_values.size > 0:
        min_value = float(valid_values.min())
        max_value = float(valid_values.max())
    else:
        min_value, max_value = 0.0, 1.0

    if symbology == "water_table":
        apply_water_table_symbology(raster_layer, min_value, max_value)
    elif symbology == "lnapl":
        apply_lnapl_symbology(raster_layer)
    elif symbology == "delta":
        apply_delta_symbology(raster_layer, min_value, max_value)
    else:
        utils.log("Simbologia '{0}' non riconosciuta, uso simbologia di default.".format(symbology), level="WARNING")

    if add_to_project:
        QgsProject.instance().addMapLayer(raster_layer)

    utils.log("Layer raster creato: {0} ({1})".format(layer_name, output_path), level="SUCCESS")
    return raster_layer


# ---------------------------------------------------------------------------
# Isolinee (contour) da raster
# ---------------------------------------------------------------------------

def generate_contours(raster_layer, output_dir, layer_name, levels=None, interval=None,
                       add_to_project=True):
    """Genera un layer vettoriale di isolinee da un raster, usando
    l'algoritmo nativo GDAL 'gdal:contour' tramite il framework Processing
    di QGIS (nessuna reimplementazione manuale necessaria).

    :param raster_layer: QgsRasterLayer da cui generare le isolinee
    :param output_dir: cartella di output
    :param layer_name: nome del layer isolinee risultante
    :param levels: lista di valori espliciti per le isolinee (es. soglie
        LNAPL); se fornito ha priorita' su 'interval'
    :param interval: intervallo costante tra isolinee (alternativa a 'levels',
        usato tipicamente per le curve isopiezometriche)
    :param add_to_project: se True, aggiunge il layer al progetto
    :return: QgsVectorLayer delle isolinee
    :raises LayerGenerationError: se l'algoritmo Processing fallisce
    """
    os.makedirs(output_dir, exist_ok=True)
    safe_name = utils.sanitize_layer_name(layer_name)
    output_path = os.path.join(output_dir, "{0}.gpkg".format(safe_name))

    params = {
        "INPUT": raster_layer,
        "BAND": 1,
        "FIELD_NAME": "valore",
        "CREATE_3D": False,
        "IGNORE_NODATA": False,
        "OUTPUT": output_path,
    }

    if levels:
        # gdal:contour accetta NLEVELS o un intervallo fisso; per livelli
        # espliciti non equidistanti si usa gdal:contour con INTERVAL=None
        # e si passano i livelli tramite l'opzione FIXED_LEVELS quando
        # disponibile nella versione QGIS, altrimenti si itera su un
        # intervallo minimo che copra i livelli richiesti.
        params["EXTRA"] = "-fl " + " ".join(str(lv) for lv in levels)
        params["INTERVAL"] = 0
    else:
        params["INTERVAL"] = interval if interval else 0.25  # default 25 cm per falda

    try:
        result = processing.run("gdal:contour", params)
    except Exception as exc:
        raise LayerGenerationError(
            "Errore durante la generazione delle isolinee (gdal:contour): {0}".format(exc)
        )

    output_layer_path = result.get("OUTPUT", output_path)
    contour_layer = QgsVectorLayer(output_layer_path, layer_name, "ogr")
    if not contour_layer.isValid():
        raise LayerGenerationError(
            "Il layer isolinee generato non e' valido: {0}".format(output_layer_path)
        )

    # Simbologia lineare semplice (linee sottili, colore neutro, etichettabile)
    symbol = QgsLineSymbol.createSimple({
        "color": "70,70,70,255",
        "width": "0.4",
    })
    contour_layer.renderer().setSymbol(symbol)
    contour_layer.triggerRepaint()

    if add_to_project:
        QgsProject.instance().addMapLayer(contour_layer)

    utils.log("Layer isolinee creato: {0}".format(layer_name), level="SUCCESS")
    return contour_layer


# ---------------------------------------------------------------------------
# Layer punti piezometri classificati (presenza/assenza LNAPL)
# ---------------------------------------------------------------------------

def create_classified_points_layer(points, crs_wkt, layer_name, add_to_project=True):
    """Crea un layer vettoriale puntuale dei piezometri con attributi
    completi (quota falda, spessore LNAPL, classe) e simbologia
    categorizzata per classe LNAPL.

    :param points: lista di dict (output di hydro_calculations.build_point_dataset(),
        con classificazione applicata da lnapl_processing.classify_points())
    :param crs_wkt: WKT del sistema di riferimento
    :param layer_name: nome del layer risultante
    :param add_to_project: se True, aggiunge il layer al progetto
    :return: QgsVectorLayer creato (layer di memoria)
    :raises LayerGenerationError: se non e' possibile creare il layer
    """
    crs = QgsCoordinateReferenceSystem()
    if crs_wkt:
        crs.createFromWkt(crs_wkt)

    uri = "Point?crs={0}".format(crs.authid() if crs.isValid() else "EPSG:4326")
    layer = QgsVectorLayer(uri, layer_name, "memory")
    if not layer.isValid():
        raise LayerGenerationError("Impossibile creare il layer puntuale '{0}'.".format(layer_name))

    provider = layer.dataProvider()
    fields = QgsFields()
    fields.append(QgsField("nome", QVariant.String))
    fields.append(QgsField("quota_testa", QVariant.Double))
    fields.append(QgsField("soggiacenza", QVariant.Double))
    fields.append(QgsField("quota_falda", QVariant.Double))
    fields.append(QgsField("spessore_lnapl_m", QVariant.Double))
    fields.append(QgsField("spessore_lnapl_cm", QVariant.Double))
    fields.append(QgsField("classe_lnapl", QVariant.String))
    fields.append(QgsField("presenza_lnapl", QVariant.String))
    provider.addAttributes(fields)
    layer.updateFields()

    features = []
    for point in points:
        feature = QgsFeature(layer.fields())
        feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(point["x"], point["y"])))
        thickness_m = point.get("spessore_lnapl_m", 0.0) or 0.0
        feature.setAttributes([
            point.get("nome"),
            point.get("quota_testa"),
            point.get("soggiacenza"),
            point.get("quota_falda"),
            thickness_m,
            thickness_m * 100.0,
            point.get("lnapl_class", "assente"),
            "Sì" if lnapl_processing.has_lnapl(point) else "No",
        ])
        features.append(feature)

    provider.addFeatures(features)
    layer.updateExtents()

    _apply_classified_points_symbology(layer)

    if add_to_project:
        QgsProject.instance().addMapLayer(layer)

    utils.log("Layer punti classificati creato: {0} ({1} piezometri)".format(layer_name, len(points)), level="SUCCESS")
    return layer


def _apply_classified_points_symbology(layer):
    """Applica una simbologia categorizzata al layer punti, in base al
    campo 'classe_lnapl', coerente con i colori usati per il raster LNAPL.

    :param layer: QgsVectorLayer con campo "classe_lnapl"
    """
    class_colors = {
        "assente": "#3388ff",        # blu: nessun LNAPL, punto di controllo normale
        "lieve": "#ffff00",          # giallo
        "moderato": "#ff8c00",       # arancione
        "significativo": "#ff0000",  # rosso
    }
    class_labels = {
        "assente": "Assente",
        "lieve": "Lieve (0-5 cm)",
        "moderato": "Moderato (5-20 cm)",
        "significativo": "Significativo (>20 cm)",
    }

    categories = []
    for class_value in lnapl_processing.LNAPL_CLASS_ORDER:
        symbol = QgsMarkerSymbol.createSimple({
            "name": "circle",
            "color": class_colors[class_value],
            "outline_color": "0,0,0,255",
            "outline_width": "0.4",
            "size": "3.5" if class_value != "assente" else "3.0",
        })
        category = QgsRendererCategory(class_value, symbol, class_labels[class_value])
        categories.append(category)

    renderer = QgsCategorizedSymbolRenderer("classe_lnapl", categories)
    layer.setRenderer(renderer)
    layer.triggerRepaint()


# ---------------------------------------------------------------------------
# Layer raster di variazione (Delta) tra due campagne
# ---------------------------------------------------------------------------

def create_delta_raster_from_grids(interp_result_1, interp_result_2, output_dir, layer_name, crs_wkt,
                                     add_to_project=True):
    """Crea un raster di variazione (Delta = griglia_2 - griglia_1) a
    partire da due risultati di interpolazione sulla STESSA estensione
    e risoluzione (raccomandato: usare la stessa extent/cell_size per
    entrambe le campagne, gestito automaticamente da mainPlugin.py).

    :param interp_result_1: risultato interpolate_grid() per la data 1
    :param interp_result_2: risultato interpolate_grid() per la data 2
    :param output_dir: cartella di output
    :param layer_name: nome layer risultante (es. "Delta_Falda_..._vs_...")
    :param crs_wkt: WKT del sistema di riferimento
    :param add_to_project: se True, aggiunge il layer al progetto
    :return: QgsRasterLayer del delta
    :raises LayerGenerationError: se le griglie non sono compatibili
    """
    import numpy as np

    grid_1 = interp_result_1["grid"]
    grid_2 = interp_result_2["grid"]
    nodata_1 = interp_result_1["nodata_value"]
    nodata_2 = interp_result_2["nodata_value"]

    if grid_1.shape != grid_2.shape:
        raise LayerGenerationError(
            "Le griglie delle due campagne hanno dimensioni diverse "
            "({0} vs {1}): impossibile calcolare la variazione. Verificare "
            "che extent e dimensione cella siano coerenti tra le due interpolazioni."
            .format(grid_1.shape, grid_2.shape)
        )

    nodata_out = -9999.0
    valid_mask = (grid_1 != nodata_1) & (grid_2 != nodata_2)

    delta_grid = np.full(grid_1.shape, nodata_out, dtype="float32")
    delta_grid[valid_mask] = grid_2[valid_mask] - grid_1[valid_mask]

    delta_result = {
        "grid": delta_grid,
        "extent": interp_result_1["extent"],
        "cell_size": interp_result_1["cell_size"],
        "nodata_value": nodata_out,
    }

    return create_raster_layer(
        delta_result, output_dir, layer_name, crs_wkt,
        symbology="delta", add_to_project=add_to_project,
    )


def create_delta_points_layer(delta_records, crs_wkt, layer_name, value_label="delta",
                                add_to_project=True):
    """Crea un layer puntuale con i valori di variazione (Delta) calcolati
    da hydro_calculations.build_delta_dataset(), utile come riferimento
    puntuale accanto al raster di variazione interpolato.

    :param delta_records: lista di dict (output di build_delta_dataset())
    :param crs_wkt: WKT del sistema di riferimento
    :param layer_name: nome layer risultante
    :param value_label: etichetta descrittiva del campo delta (es. "falda" o "lnapl")
    :param add_to_project: se True, aggiunge il layer al progetto
    :return: QgsVectorLayer creato
    """
    crs = QgsCoordinateReferenceSystem()
    if crs_wkt:
        crs.createFromWkt(crs_wkt)

    uri = "Point?crs={0}".format(crs.authid() if crs.isValid() else "EPSG:4326")
    layer = QgsVectorLayer(uri, layer_name, "memory")
    if not layer.isValid():
        raise LayerGenerationError("Impossibile creare il layer puntuale '{0}'.".format(layer_name))

    provider = layer.dataProvider()
    fields = QgsFields()
    fields.append(QgsField("nome", QVariant.String))
    fields.append(QgsField("valore_1", QVariant.Double))
    fields.append(QgsField("valore_2", QVariant.Double))
    fields.append(QgsField("delta_{0}".format(utils.sanitize_layer_name(value_label)), QVariant.Double))
    provider.addAttributes(fields)
    layer.updateFields()

    features = []
    for record in delta_records:
        feature = QgsFeature(layer.fields())
        feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(record["x"], record["y"])))
        feature.setAttributes([
            record["nome"],
            record["value_1"],
            record["value_2"],
            record["delta"],
        ])
        features.append(feature)

    provider.addFeatures(features)
    layer.updateExtents()

    if add_to_project:
        QgsProject.instance().addMapLayer(layer)

    utils.log("Layer punti variazione creato: {0}".format(layer_name), level="SUCCESS")
    return layer
