# -*- coding: utf-8 -*-
"""
mainPlugin.py
==============

Classe principale del plugin "HydroScope".

Responsabilita':
- registrazione del plugin nel menu e nella toolbar di QGIS (initGui/unload)
- gestione dello stato applicativo (dati importati, layer GIS selezionato)
- orchestrazione del flusso completo: import -> calcolo quota falda ->
  classificazione LNAPL -> interpolazione -> creazione layer
- collegamento dei segnali della dialog (dialog.py) alle azioni effettive

Questo modulo e' l'unico punto in cui tutti gli altri moduli vengono
messi in comunicazione tra loro.
"""

import os

from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtGui import QIcon
from qgis.core import QgsProject

from . import utils
from . import excel_import
from . import hydro_calculations as hc
from . import lnapl_processing as lp
from . import interpolation as interp
from . import layer_generator as lg
from .dialog import HydroScopeDialog


class HydroScopePlugin:
    """Classe principale del plugin, istanziata da classFactory() in __init__.py."""

    def __init__(self, iface):
        """
        :param iface: interfaccia QGIS (QgisInterface) fornita da QGIS al
            caricamento del plugin
        """
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.actions = []
        self.menu = "&HydroScope"
        self.dialog = None

        # Stato dati corrente (popolato dopo l'import del file)
        self.dataset = None             # output di excel_import.read_monitoring_file()
        self.piezometers = {}           # anagrafica unificata {nome: {...}}
        self.output_dir = None          # cartella di output per i file raster generati

    # ------------------------------------------------------------------
    # Ciclo di vita del plugin (richiesto da QGIS)
    # ------------------------------------------------------------------

    def initGui(self):
        """Chiamato da QGIS all'attivazione del plugin: crea il menu e
        l'icona in toolbar."""
        icon_path = os.path.join(self.plugin_dir, "icons", "icon.png")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()

        action = QAction(icon, "HydroScope", self.iface.mainWindow())
        action.triggered.connect(self.run)
        action.setEnabled(True)

        self.iface.addToolBarIcon(action)
        self.iface.addPluginToMenu(self.menu, action)
        self.actions.append(action)

    def unload(self):
        """Chiamato da QGIS alla disattivazione/disinstallazione del plugin:
        rimuove menu e toolbar."""
        for action in self.actions:
            self.iface.removePluginMenu(self.menu, action)
            self.iface.removeToolBarIcon(action)
        self.actions = []

        if self.dialog is not None:
            self.dialog.close()
            self.dialog = None

    def run(self):
        """Apre la finestra di dialogo principale del plugin (azione del
        bottone/menu)."""
        if self.dialog is None:
            self.dialog = HydroScopeDialog(self.iface.mainWindow())
            self.dialog.request_import_file.connect(self._on_import_file)
            self.dialog.request_generate_water_table.connect(self._on_generate_water_table)
            self.dialog.request_generate_lnapl.connect(self._on_generate_lnapl)
            self.dialog.request_compare_campaigns.connect(self._on_compare_campaigns)

        self._prepare_output_dir()
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    # ------------------------------------------------------------------
    # Setup cartella output
    # ------------------------------------------------------------------

    def _prepare_output_dir(self):
        """Determina e crea (se necessario) la cartella di output dove
        verranno scritti i file raster/vettoriali generati. Viene usata
        una sotto-cartella del progetto QGIS corrente, se disponibile,
        altrimenti la home utente."""
        project_path = QgsProject.instance().fileName()
        if project_path:
            base_dir = os.path.dirname(project_path)
        else:
            base_dir = os.path.expanduser("~")

        self.output_dir = os.path.join(base_dir, "hydroscope_output")
        os.makedirs(self.output_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Gestione import file
    # ------------------------------------------------------------------

    def _on_import_file(self, filepath):
        """Gestisce la richiesta di import del file Excel/CSV dalla dialog."""
        try:
            dataset = excel_import.read_monitoring_file(filepath)
        except excel_import.ImportError_ as exc:
            self.dialog.set_import_status("Errore di importazione.", success=False)
            self.dialog.show_error("Errore importazione file", str(exc))
            return
        except Exception as exc:
            self.dialog.set_import_status("Errore inatteso.", success=False)
            self.dialog.show_error("Errore importazione file", "Errore inatteso: {0}".format(exc))
            return

        self.dataset = dataset

        # Costruzione anagrafica piezometri unificata, in base alla modalita' scelta
        input_mode = self.dialog.get_input_mode()
        try:
            if dataset["mode"] == "full":
                self.piezometers = dataset["piezometers"]
            else:
                if input_mode != "layer_plus_file":
                    raise hc.HydroCalculationError(
                        "Il file importato non contiene l'anagrafica completa "
                        "(X, Y, Quota testa). Selezionare la modalita' 'Layer "
                        "piezometri GIS esistente + file Excel/CSV di campagna' "
                        "e scegliere il layer piezometri corrispondente."
                    )
                layer = self.dialog.get_selected_piezometer_layer()
                self.piezometers = hc.load_piezometers_from_layer(layer)
        except hc.HydroCalculationError as exc:
            self.dialog.set_import_status("Errore anagrafica piezometri.", success=False)
            self.dialog.show_error("Errore anagrafica piezometri", str(exc))
            return

        n_campaigns = len(dataset["campaigns"])
        n_dates = len(dataset["dates"])
        n_piezo = len(self.piezometers)

        self.dialog.set_import_status(
            "Import completato: {0} piezometri, {1} record di campagna, "
            "{2} date disponibili.".format(n_piezo, n_campaigns, n_dates),
            success=True,
        )

        date_strings = [utils.format_date_for_layer_name(d) for d in dataset["dates"]]
        self.dialog.populate_dates(date_strings)

        if dataset.get("row_errors"):
            self.dialog.append_log(
                "{0} righe scartate per errori di validazione (vedi Message Log "
                "di QGIS, categoria 'HydroScope', per il dettaglio)."
                .format(len(dataset["row_errors"]))
            )

        self.dialog.append_log("File importato: {0}".format(filepath))

    # ------------------------------------------------------------------
    # Helper: recupero CRS dal layer piezometri (se disponibile)
    # ------------------------------------------------------------------

    def _get_crs_wkt(self):
        """Determina il WKT del sistema di riferimento da assegnare ai
        layer generati.

        - Modalita' 'layer_plus_file': usa il CRS del layer piezometri
          selezionato (le sue coordinate X/Y sono gia' in quel sistema).
        - Modalita' 'full' (file unico): usa il CRS dichiarato esplicitamente
          dall'utente nella dialog per le coordinate X/Y del file. NON si
          assume piu' il CRS del progetto QGIS: le coordinate di un file
          Excel/CSV non hanno un sistema di riferimento intrinseco, e
          assumerlo automaticamente dal progetto (spesso WGS84 geografico
          di default) posizionerebbe i punti in modo errato se le
          coordinate sono, ad esempio, in metri UTM.
        """
        if self.dialog is not None and self.dialog.get_input_mode() == "layer_plus_file":
            layer = self.dialog.get_selected_piezometer_layer()
            if layer is not None and layer.crs().isValid():
                return layer.crs().toWkt()

        if self.dialog is not None:
            full_file_crs = self.dialog.get_full_file_crs()
            if full_file_crs is not None and full_file_crs.isValid():
                return full_file_crs.toWkt()

        return QgsProject.instance().crs().toWkt()

    def _get_target_dates(self, expected_count=None):
        """Recupera e valida le date selezionate dall'utente nella dialog,
        convertendole in oggetti datetime.date confrontabili con quelle
        del dataset importato.

        :param expected_count: se fornito, valida che il numero di date
            selezionate corrisponda esattamente (1 per le mappe singole,
            2 per il confronto campagne)
        :return: lista di datetime.date
        :raises ValueError: se la selezione non e' valida
        """
        if self.dataset is None:
            raise ValueError("Importare prima un file di campagna.")

        selected_strings = self.dialog.get_selected_dates()
        if expected_count is not None and len(selected_strings) != expected_count:
            raise ValueError(
                "Selezionare esattamente {0} data/e nell'elenco "
                "(attualmente selezionate: {1}).".format(expected_count, len(selected_strings))
            )
        if not selected_strings:
            raise ValueError("Selezionare almeno una data dall'elenco.")

        # Le date in dataset["dates"] sono datetime.date; le confrontiamo
        # con le stringhe selezionate tramite la stessa formattazione usata
        # per popolare la lista (vedi populate_dates in dialog.py).
        date_map = {
            utils.format_date_for_layer_name(d): d for d in self.dataset["dates"]
        }
        try:
            return [date_map[s] for s in selected_strings]
        except KeyError as exc:
            raise ValueError("Data selezionata non riconosciuta: {0}".format(exc))

    # ------------------------------------------------------------------
    # Azione: Genera mappa falda
    # ------------------------------------------------------------------

    def _on_generate_water_table(self):
        try:
            target_dates = self._get_target_dates(expected_count=1)
            target_date = target_dates[0]

            points = hc.build_point_dataset(self.piezometers, self.dataset["campaigns"], target_date)
            lp.classify_points(points)

            method = self.dialog.get_interpolation_method()
            cell_size = self.dialog.get_cell_size()
            power = self.dialog.get_idw_power()

            interp_result = interp.interpolate_grid(
                points, "quota_falda", cell_size=cell_size, method=method, power=power,
            )

            crs_wkt = self._get_crs_wkt()
            layer_name = utils.build_layer_name("Falda", target_date)

            raster_layer = lg.create_raster_layer(
                interp_result, self.output_dir, layer_name, crs_wkt, symbology="water_table",
            )

            contour_name = utils.build_layer_name("Falda", target_date, suffix="isolinee")
            lg.generate_contours(raster_layer, self.output_dir, contour_name, interval=0.25)

            points_layer_name = utils.build_layer_name("Piezometri", target_date, suffix="classificati")
            lg.create_classified_points_layer(points, crs_wkt, points_layer_name)

            self.dialog.append_log(
                "Mappa falda generata per la data {0}: layer '{1}' creato con successo."
                .format(target_date, layer_name)
            )
            self.dialog.append_log(utils.interpolation_reminder_text(method, cell_size))

        except (ValueError, hc.HydroCalculationError, interp.InterpolationError, lg.LayerGenerationError) as exc:
            self.dialog.show_error("Genera mappa falda", str(exc))
        except Exception as exc:
            utils.log("Errore inatteso in _on_generate_water_table: {0}".format(exc), level="CRITICAL")
            self.dialog.show_error("Genera mappa falda", "Errore inatteso: {0}".format(exc))

    # ------------------------------------------------------------------
    # Azione: Genera mappa LNAPL
    # ------------------------------------------------------------------

    def _on_generate_lnapl(self):
        try:
            target_dates = self._get_target_dates(expected_count=1)
            target_date = target_dates[0]

            points = hc.build_point_dataset(self.piezometers, self.dataset["campaigns"], target_date)
            lp.classify_points(points)

            summary = lp.summarize_lnapl_status(points)
            method = self.dialog.get_interpolation_method()
            cell_size = self.dialog.get_cell_size()
            power = self.dialog.get_idw_power()

            interp_result = interp.interpolate_grid(
                points, "spessore_lnapl_m", cell_size=cell_size, method=method, power=power,
                min_neighbors=1,
            )

            crs_wkt = self._get_crs_wkt()
            layer_name = utils.build_layer_name("LNAPL", target_date)

            raster_layer = lg.create_raster_layer(
                interp_result, self.output_dir, layer_name, crs_wkt, symbology="lnapl",
            )

            isopach_levels = lp.get_isopach_levels(summary["spessore_massimo_m"])
            contour_name = utils.build_layer_name("LNAPL", target_date, suffix="isopach")
            lg.generate_contours(raster_layer, self.output_dir, contour_name, levels=isopach_levels)

            points_layer_name = utils.build_layer_name("Piezometri", target_date, suffix="LNAPL")
            lg.create_classified_points_layer(points, crs_wkt, points_layer_name)

            self.dialog.append_log(
                "Mappa LNAPL generata per la data {0}: {1}/{2} piezometri con "
                "presenza di prodotto libero ({3:.0f}%). Spessore massimo: "
                "{4:.1f} cm in {5}.".format(
                    target_date, summary["n_con_lnapl"], summary["n_totale"],
                    summary["percentuale_con_lnapl"],
                    summary["spessore_massimo_m"] * 100.0,
                    summary["piezometro_massimo"] or "n/d",
                )
            )
            self.dialog.append_log(utils.interpolation_reminder_text(method, cell_size))

        except (ValueError, hc.HydroCalculationError, interp.InterpolationError, lg.LayerGenerationError) as exc:
            self.dialog.show_error("Genera mappa LNAPL", str(exc))
        except Exception as exc:
            utils.log("Errore inatteso in _on_generate_lnapl: {0}".format(exc), level="CRITICAL")
            self.dialog.show_error("Genera mappa LNAPL", "Errore inatteso: {0}".format(exc))

    # ------------------------------------------------------------------
    # Azione: Confronta campagne (Delta)
    # ------------------------------------------------------------------

    def _on_compare_campaigns(self):
        try:
            target_dates = self._get_target_dates(expected_count=2)
            date_1, date_2 = sorted(target_dates)

            points_1 = hc.build_point_dataset(self.piezometers, self.dataset["campaigns"], date_1)
            points_2 = hc.build_point_dataset(self.piezometers, self.dataset["campaigns"], date_2)
            lp.classify_points(points_1)
            lp.classify_points(points_2)

            method = self.dialog.get_interpolation_method()
            cell_size = self.dialog.get_cell_size()
            power = self.dialog.get_idw_power()
            crs_wkt = self._get_crs_wkt()

            # Stessa estensione per entrambe le interpolazioni, calcolata
            # sull'unione dei punti delle due campagne, cosi' le griglie
            # risultano comparabili cella per cella per il calcolo del Delta.
            combined_points = points_1 + points_2
            shared_extent = interp.compute_grid_extent(combined_points)

            # --- Falda --------------------------------------------------
            interp_falda_1 = interp.interpolate_grid(
                points_1, "quota_falda", cell_size=cell_size, method=method,
                power=power, extent=shared_extent,
            )
            interp_falda_2 = interp.interpolate_grid(
                points_2, "quota_falda", cell_size=cell_size, method=method,
                power=power, extent=shared_extent,
            )

            delta_falda_name = utils.build_layer_name("Delta_Falda", (date_1, date_2))
            lg.create_delta_raster_from_grids(
                interp_falda_1, interp_falda_2, self.output_dir, delta_falda_name, crs_wkt,
            )

            delta_falda_points = hc.build_delta_dataset(points_1, points_2, "quota_falda")
            delta_falda_points_name = utils.build_layer_name(
                "Delta_Falda", (date_1, date_2), suffix="punti"
            )
            lg.create_delta_points_layer(delta_falda_points, crs_wkt, delta_falda_points_name, value_label="falda")

            # --- LNAPL ----------------------------------------------------
            interp_lnapl_1 = interp.interpolate_grid(
                points_1, "spessore_lnapl_m", cell_size=cell_size, method=method,
                power=power, extent=shared_extent,
            )
            interp_lnapl_2 = interp.interpolate_grid(
                points_2, "spessore_lnapl_m", cell_size=cell_size, method=method,
                power=power, extent=shared_extent,
            )

            delta_lnapl_name = utils.build_layer_name("Delta_LNAPL", (date_1, date_2))
            lg.create_delta_raster_from_grids(
                interp_lnapl_1, interp_lnapl_2, self.output_dir, delta_lnapl_name, crs_wkt,
            )

            delta_lnapl_points = hc.build_delta_dataset(points_1, points_2, "spessore_lnapl_m")
            delta_lnapl_points_name = utils.build_layer_name(
                "Delta_LNAPL", (date_1, date_2), suffix="punti"
            )
            lg.create_delta_points_layer(delta_lnapl_points, crs_wkt, delta_lnapl_points_name, value_label="lnapl")

            self.dialog.append_log(
                "Confronto campagne completato: {0} vs {1}. Layer creati: "
                "'{2}', '{3}'.".format(date_1, date_2, delta_falda_name, delta_lnapl_name)
            )
            self.dialog.append_log(utils.interpolation_reminder_text(method, cell_size))

        except (ValueError, hc.HydroCalculationError, interp.InterpolationError, lg.LayerGenerationError) as exc:
            self.dialog.show_error("Confronta campagne", str(exc))
        except Exception as exc:
            utils.log("Errore inatteso in _on_compare_campaigns: {0}".format(exc), level="CRITICAL")
            self.dialog.show_error("Confronta campagne", "Errore inatteso: {0}".format(exc))
