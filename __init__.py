# -*- coding: utf-8 -*-
"""
HydroScope
====================

Plugin QGIS 3.x per la gestione e analisi di dati piezometrici in siti
contaminati, con particolare attenzione alla falda e al LNAPL (Light
Non-Aqueous Phase Liquid).

Questo file e' il punto di ingresso richiesto da QGIS. La funzione
classFactory() viene chiamata da QGIS al momento del caricamento del
plugin e deve restituire l'istanza della classe principale del plugin.

Non modificare la firma di classFactory(): e' parte del contratto
QGIS Plugin Builder / QGIS Plugin API.
"""

__author__ = "Filippo Graziano"
__copyright__ = "Copyright 2026"


def classFactory(iface):
    """Punto di ingresso del plugin, chiamato da QGIS al caricamento.

    :param iface: Interfaccia QGIS (QgisInterface) fornita da QGIS.
    :type iface: QgsInterface
    :return: Istanza della classe principale del plugin.
    :rtype: HydroScopePlugin
    """
    from .mainPlugin import HydroScopePlugin
    return HydroScopePlugin(iface)
