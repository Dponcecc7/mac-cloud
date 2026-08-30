# -*- coding: utf-8 -*-
from excel_safety import fila_segura, texto_seguro_excel


def test_deja_texto_normal_intacto():
    assert texto_seguro_excel("Juan Perez") == "Juan Perez"
    assert texto_seguro_excel("") == ""


def test_none_pasa_igual():
    assert texto_seguro_excel(None) is None


def test_numeros_y_otros_tipos_pasan_igual():
    assert texto_seguro_excel(5) == 5
    assert texto_seguro_excel(3.14) == 3.14


def test_antepone_apostrofe_a_formulas():
    # OWASP CSV/Excel injection -- estos 4 caracteres son los que Excel
    # interpreta como inicio de formula.
    assert texto_seguro_excel("=cmd|'/c calc'!A1") == "'=cmd|'/c calc'!A1"
    assert texto_seguro_excel("+1+1") == "'+1+1"
    assert texto_seguro_excel("-1+1") == "'-1+1"
    assert texto_seguro_excel("@SUM(A1)") == "'@SUM(A1)"


def test_no_afecta_texto_que_solo_contiene_esos_caracteres_en_el_medio():
    assert texto_seguro_excel("Falta - sin motivo") == "Falta - sin motivo"


def test_fila_segura_sanea_cada_valor_de_la_lista():
    assert fila_segura(["=1+1", "normal", None, 5, "@riesgo"]) == [
        "'=1+1", "normal", None, 5, "'@riesgo",
    ]


def test_fila_segura_lista_vacia():
    assert fila_segura([]) == []
