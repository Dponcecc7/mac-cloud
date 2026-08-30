# -*- coding: utf-8 -*-
"""athena_client.py es importable sin credenciales de AWS -- conectar() (lo
unico que las necesita) no se llama al importar el modulo, solo al
correr traer_visitas() de verdad."""
import math

from athena_client import (
    CADENAS_AUTOSERVICIO, CADENAS_FARMACIA, CAMPANA_ID_A_TIPO_NEGOCIO,
    cadena_a_tipo_negocio, haversine_m, parse_latlon,
)


def test_campana_id_a_tipo_negocio_mapea_los_ids_conocidos():
    # Root cause del bug real de Cobertura (Davor, 2026-08-26/29): estos
    # ids son la fuente confiable de canal por visita, no la cadena.
    assert CAMPANA_ID_A_TIPO_NEGOCIO[255] == "FARMACIA"
    assert CAMPANA_ID_A_TIPO_NEGOCIO[10] == "AUTOSERVICIOS"
    assert CAMPANA_ID_A_TIPO_NEGOCIO[924] == "TRADICIONAL"


def test_cadena_a_tipo_negocio_reconoce_autoservicio():
    for cadena in CADENAS_AUTOSERVICIO:
        assert cadena_a_tipo_negocio(cadena) == "AUTOSERVICIOS"


def test_cadena_a_tipo_negocio_reconoce_farmacia():
    for cadena in CADENAS_FARMACIA:
        assert cadena_a_tipo_negocio(cadena) == "FARMACIA"


def test_cadena_a_tipo_negocio_default_tradicional():
    assert cadena_a_tipo_negocio("UNA CADENA CUALQUIERA NO LISTADA") == "TRADICIONAL"


def test_cadena_a_tipo_negocio_normaliza_mayusculas_y_espacios():
    assert cadena_a_tipo_negocio("  plaza vea  ") == "AUTOSERVICIOS"


def test_parse_latlon_valido():
    lat, lon = parse_latlon("-12.0464,-77.0428")
    assert lat == -12.0464
    assert lon == -77.0428


def test_parse_latlon_invalido_devuelve_none_none():
    assert parse_latlon("texto sin formato") == (None, None)
    assert parse_latlon(None) == (None, None)


def test_haversine_distancia_cero_para_el_mismo_punto():
    assert haversine_m(-12.0, -77.0, -12.0, -77.0) == 0


def test_haversine_devuelve_none_si_falta_un_dato():
    assert haversine_m(None, -77.0, -12.0, -77.0) is None
    assert haversine_m(-12.0, -77.0, -12.0, None) is None


def test_haversine_distancia_conocida_aprox():
    # ~1 grado de latitud son ~111 km en el ecuador -- chequeo de orden de
    # magnitud, no un valor exacto (no es el objetivo de este test).
    d = haversine_m(0.0, 0.0, 1.0, 0.0)
    assert 110_000 < d < 112_000
