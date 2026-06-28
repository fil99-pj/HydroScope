# -*- coding: utf-8 -*-
"""
resources.py
=============

Modulo risorse del plugin.

NOTA IMPORTANTE: questo plugin carica le icone direttamente dal filesystem
(vedi mainPlugin.py, funzione initGui(), che usa
`os.path.join(self.plugin_dir, "icons", "icon.png")`), quindi NON dipende
dalla compilazione del file resources.qrc tramite pyrcc5.

Questo file e' incluso solo per coerenza con la struttura standard di un
plugin QGIS (Plugin Builder) e per consentire, in futuro, l'uso del
sistema di risorse Qt (qrc) se si preferisce incorporare le icone nel
bytecode Python invece di leggerle da file.

Per generare la versione "vera" e completa di questo file con tutte le
risorse incorporate come bytecode Qt, eseguire dalla cartella del plugin
(con l'SDK Qt disponibile, es. tramite l'OSGeo4W Shell di QGIS su Windows
o il pacchetto qt5-tools su Linux):

    pyrcc5 -o resources.py resources.qrc

Questo sovrascrivera' il presente file con la versione compilata. Non e'
un passaggio obbligatorio per il funzionamento del plugin.
"""

# Percorso relativo della cartella icone, utile se altri moduli vogliono
# riferirsi alle risorse senza ricostruire il path manualmente.
ICON_PATH = "icons/icon.png"
