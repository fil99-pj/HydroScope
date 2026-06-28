# -*- coding: utf-8 -*-
"""
dialog.py
=========

Interfaccia utente del plugin, costruita in PyQt5 puro (nessun file .ui /
Qt Designer): piu' semplice da mantenere e debuggare.

La finestra di dialogo offre:
- selezione della modalita' di input (file unico / layer GIS + Excel campagna)
- selezione layer piezometri esistente (se modalita' 2)
- import del file Excel/CSV di campagna
- selezione della/e data/e di campagna su cui operare
- scelta del metodo di interpolazione (IDW di default, Kriging disabilitato)
- parametri di interpolazione (dimensione cella)
- bottoni per le azioni principali:
    "Genera mappa falda", "Genera mappa LNAPL", "Confronta campagne"
- area di log/messaggi per dare riscontro immediato all'utente

Questo modulo dipende da PyQt5 e da qgis.gui per i widget di selezione
layer (QgsMapLayerComboBox), ma NON contiene logica di elaborazione: si
limita a raccogliere gli input e a delegare tutto a mainPlugin.py tramite
callback, mantenendo la UI disaccoppiata dalla business logic.
"""

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QGroupBox,
    QRadioButton,
    QLabel,
    QLineEdit,
    QPushButton,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QTextEdit,
    QButtonGroup,
    QListWidget,
    QAbstractItemView,
    QMessageBox,
    QTabWidget,
    QWidget,
    QSizePolicy,
)
from qgis.gui import QgsMapLayerComboBox, QgsProjectionSelectionWidget
from qgis.core import QgsMapLayerProxyModel, QgsCoordinateReferenceSystem, QgsProject


class HydroScopeDialog(QDialog):
    """Finestra di dialogo principale del plugin HydroScope."""

    # Segnali emessi quando l'utente clicca un bottone di azione; mainPlugin.py
    # si collega a questi segnali per eseguire l'elaborazione effettiva.
    request_generate_water_table = pyqtSignal()
    request_generate_lnapl = pyqtSignal()
    request_compare_campaigns = pyqtSignal()
    request_import_file = pyqtSignal(str)  # percorso file scelto

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("HydroScope")
        self.setMinimumWidth(560)
        self.setMinimumHeight(620)

        self._build_ui()

    # ------------------------------------------------------------------
    # Costruzione UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        main_layout = QVBoxLayout(self)

        main_layout.addWidget(self._build_disclaimer_label())
        main_layout.addWidget(self._build_input_mode_group())
        main_layout.addWidget(self._build_import_group())
        main_layout.addWidget(self._build_dates_group())
        main_layout.addWidget(self._build_interpolation_group())
        main_layout.addWidget(self._build_actions_group())
        main_layout.addWidget(self._build_log_group())

        close_btn = QPushButton("Chiudi")
        close_btn.clicked.connect(self.close)
        main_layout.addWidget(close_btn, alignment=Qt.AlignRight)

    def _build_disclaimer_label(self):
        label = QLabel(
            "⚠️ Strumento di prima interpretazione esplorativa dei dati. "
            "I risultati dipendono dai parametri di interpolazione scelti "
            "e non sostituiscono la valutazione di un tecnico competente."
        )
        label.setWordWrap(True)
        label.setStyleSheet(
            "background-color: #fff3cd; color: #664d03; "
            "border: 1px solid #ffe69c; border-radius: 4px; padding: 6px;"
        )
        return label

    def _build_input_mode_group(self):
        group = QGroupBox("1. Modalita' di input dati piezometri")
        layout = QVBoxLayout(group)

        self.radio_full_file = QRadioButton(
            "File Excel/CSV unico (anagrafica + dati di campagna)"
        )
        self.radio_layer_plus_file = QRadioButton(
            "Layer piezometri GIS esistente + file Excel/CSV di campagna"
        )
        self.radio_full_file.setChecked(True)

        self.mode_button_group = QButtonGroup(self)
        self.mode_button_group.addButton(self.radio_full_file, 0)
        self.mode_button_group.addButton(self.radio_layer_plus_file, 1)

        layout.addWidget(self.radio_full_file)
        layout.addWidget(self.radio_layer_plus_file)

        # Selettore layer piezometri (visibile/abilitato solo in modalita' 2)
        layer_row = QHBoxLayout()
        layer_row.addWidget(QLabel("Layer piezometri:"))
        self.piezometer_layer_combo = QgsMapLayerComboBox()
        self.piezometer_layer_combo.setFilters(QgsMapLayerProxyModel.PointLayer)
        self.piezometer_layer_combo.setEnabled(False)
        layer_row.addWidget(self.piezometer_layer_combo)
        layout.addLayout(layer_row)

        # Selettore CRS delle coordinate X/Y nel file (richiesto SOLO in
        # modalita' "file unico": qui le coordinate non hanno un CRS
        # associato in modo automatico, a differenza di un layer GIS
        # esistente che lo dichiara gia'. Va specificato esplicitamente
        # per evitare di posizionare i punti nel posto sbagliato (es. se
        # le coordinate sono in metri UTM ma vengono interpretate come
        # gradi WGS84 del progetto).
        self.crs_row_label = QLabel(
            "Sistema di riferimento (CRS) delle coordinate X/Y nel file:"
        )
        layout.addWidget(self.crs_row_label)
        self.crs_selector = QgsProjectionSelectionWidget()
        # Default ragionevole: il CRS del progetto corrente, se gia' valido
        # e non geografico; altrimenti WGS84 UTM Zone 32N (Italia centro-nord)
        project_crs = QgsProject.instance().crs()
        if project_crs.isValid() and not project_crs.isGeographic():
            self.crs_selector.setCrs(project_crs)
        else:
            self.crs_selector.setCrs(QgsCoordinateReferenceSystem("EPSG:32632"))
        layout.addWidget(self.crs_selector)

        self.radio_full_file.toggled.connect(self._on_input_mode_changed)
        self.radio_layer_plus_file.toggled.connect(self._on_input_mode_changed)

        return group

    def _build_import_group(self):
        group = QGroupBox("2. Import file Excel/CSV di campagna")
        layout = QVBoxLayout(group)

        file_row = QHBoxLayout()
        self.file_path_edit = QLineEdit()
        self.file_path_edit.setReadOnly(True)
        self.file_path_edit.setPlaceholderText("Nessun file selezionato...")
        browse_btn = QPushButton("Sfoglia...")
        browse_btn.clicked.connect(self._on_browse_file)
        file_row.addWidget(self.file_path_edit)
        file_row.addWidget(browse_btn)
        layout.addLayout(file_row)

        import_btn = QPushButton("Importa file")
        import_btn.clicked.connect(self._on_import_clicked)
        layout.addWidget(import_btn)

        self.import_status_label = QLabel("")
        self.import_status_label.setWordWrap(True)
        layout.addWidget(self.import_status_label)

        return group

    def _build_dates_group(self):
        group = QGroupBox("3. Selezione data/e campagna")
        layout = QVBoxLayout(group)

        layout.addWidget(QLabel(
            "Seleziona una data per generare le mappe singole, oppure due "
            "date per il confronto tra campagne (Ctrl+click per multi-selezione):"
        ))
        self.dates_list = QListWidget()
        self.dates_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.dates_list.setMaximumHeight(120)
        layout.addWidget(self.dates_list)

        return group

    def _build_interpolation_group(self):
        group = QGroupBox("4. Parametri di interpolazione")
        layout = QFormLayout(group)

        self.method_combo = QComboBox()
        self.method_combo.addItem("IDW (Inverse Distance Weighting) - consigliato", "idw")
        self.method_combo.addItem("Kriging (Ordinary Kriging, modello automatico)", "kriging")
        self.method_combo.currentIndexChanged.connect(self._on_method_changed)
        layout.addRow("Metodo:", self.method_combo)

        self.cell_size_spin = QDoubleSpinBox()
        self.cell_size_spin.setRange(0.1, 1000.0)
        self.cell_size_spin.setDecimals(2)
        self.cell_size_spin.setValue(1.0)
        self.cell_size_spin.setSuffix(" m")
        layout.addRow("Dimensione cella raster:", self.cell_size_spin)

        self.power_spin_label = QLabel("Esponente IDW (potenza):")
        self.power_spin = QDoubleSpinBox()
        self.power_spin.setRange(0.5, 6.0)
        self.power_spin.setSingleStep(0.5)
        self.power_spin.setValue(2.0)
        layout.addRow(self.power_spin_label, self.power_spin)

        self.kriging_info_label = QLabel(
            "Il modello di variogramma (sferico, esponenziale, gaussiano o "
            "lineare) viene scelto automaticamente per cross-validation. "
            "Con meno di 10 piezometri la stima puo' essere poco "
            "affidabile: verificare il Message Log di QGIS per il dettaglio "
            "del modello scelto e l'errore stimato."
        )
        self.kriging_info_label.setWordWrap(True)
        self.kriging_info_label.setVisible(False)
        layout.addRow(self.kriging_info_label)

        self.generate_contours_check_label = QLabel(
            "Le isolinee (curve isopiezometriche / isopach LNAPL) vengono "
            "generate automaticamente insieme ai raster."
        )
        self.generate_contours_check_label.setWordWrap(True)
        layout.addRow(self.generate_contours_check_label)

        return group

    def _build_actions_group(self):
        group = QGroupBox("5. Azioni")
        layout = QVBoxLayout(group)

        self.btn_generate_water_table = QPushButton("Genera mappa falda")
        self.btn_generate_lnapl = QPushButton("Genera mappa LNAPL")
        self.btn_compare_campaigns = QPushButton("Confronta campagne (Δ)")

        for btn in (
            self.btn_generate_water_table,
            self.btn_generate_lnapl,
            self.btn_compare_campaigns,
        ):
            btn.setMinimumHeight(34)

        self.btn_generate_water_table.clicked.connect(self.request_generate_water_table.emit)
        self.btn_generate_lnapl.clicked.connect(self.request_generate_lnapl.emit)
        self.btn_compare_campaigns.clicked.connect(self.request_compare_campaigns.emit)

        layout.addWidget(self.btn_generate_water_table)
        layout.addWidget(self.btn_generate_lnapl)
        layout.addWidget(self.btn_compare_campaigns)

        return group

    def _build_log_group(self):
        group = QGroupBox("Messaggi")
        layout = QVBoxLayout(group)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(120)
        layout.addWidget(self.log_text)

        return group

    # ------------------------------------------------------------------
    # Slot interni
    # ------------------------------------------------------------------

    def _on_input_mode_changed(self):
        is_layer_mode = self.radio_layer_plus_file.isChecked()
        self.piezometer_layer_combo.setEnabled(is_layer_mode)
        # Il CRS va dichiarato SOLO in modalita' "file unico": in modalita'
        # "layer GIS esistente" il CRS si legge automaticamente dal layer.
        self.crs_row_label.setVisible(not is_layer_mode)
        self.crs_selector.setVisible(not is_layer_mode)

    def _on_method_changed(self):
        is_kriging = self.method_combo.currentData() == "kriging"
        self.power_spin_label.setVisible(not is_kriging)
        self.power_spin.setVisible(not is_kriging)
        self.kriging_info_label.setVisible(is_kriging)

    def _on_browse_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Seleziona file di campagna",
            "",
            "Excel/CSV (*.xlsx *.xlsm *.csv);;Tutti i file (*.*)",
        )
        if path:
            self.file_path_edit.setText(path)

    def _on_import_clicked(self):
        path = self.file_path_edit.text().strip()
        if not path:
            QMessageBox.warning(self, "HydroScope", "Selezionare un file da importare.")
            return
        self.request_import_file.emit(path)

    # ------------------------------------------------------------------
    # API pubblica per mainPlugin.py
    # ------------------------------------------------------------------

    def get_input_mode(self):
        """Restituisce 'full' o 'layer_plus_file' in base al radio selezionato."""
        return "full" if self.radio_full_file.isChecked() else "layer_plus_file"

    def get_selected_piezometer_layer(self):
        """Restituisce il QgsVectorLayer selezionato nel combo (modalita' 2)."""
        return self.piezometer_layer_combo.currentLayer()

    def get_full_file_crs(self):
        """Restituisce il QgsCoordinateReferenceSystem dichiarato dall'utente
        per le coordinate X/Y del file Excel/CSV in modalita' 'file unico'."""
        return self.crs_selector.crs()

    def get_interpolation_method(self):
        return self.method_combo.currentData()

    def get_cell_size(self):
        return self.cell_size_spin.value()

    def get_idw_power(self):
        return self.power_spin.value()

    def populate_dates(self, dates):
        """Popola la lista delle date disponibili dopo un import riuscito.

        :param dates: lista di stringhe (date formattate) da mostrare
        """
        self.dates_list.clear()
        for date_str in dates:
            self.dates_list.addItem(date_str)

    def get_selected_dates(self):
        """Restituisce le date selezionate dall'utente nella lista (testo)."""
        return [item.text() for item in self.dates_list.selectedItems()]

    def set_import_status(self, message, success=True):
        """Aggiorna l'etichetta di stato import con un messaggio breve."""
        color = "#1e7e34" if success else "#c0392b"
        self.import_status_label.setText(
            "<span style='color:{0};'>{1}</span>".format(color, message)
        )

    def append_log(self, message):
        """Aggiunge una riga al pannello messaggi della dialog."""
        self.log_text.append(message)

    def show_error(self, title, message):
        QMessageBox.critical(self, title, message)

    def show_info(self, title, message):
        QMessageBox.information(self, title, message)

    def show_warning(self, title, message):
        QMessageBox.warning(self, title, message)
