"""
core/extractor.py
Extração de texto de PDFs e parsing de dados de bureau (Serasa / Neoway) e DRE.
"""

import re
import warnings
from typing import Dict, Optional, Tuple

import pdfplumber
import streamlit as st

warnings.filterwarnings("ignore")


# ── 1. Extração de texto ──────────────────────────────────────────────────────

def extract_text_from_pdf(pdf_file) -> str:
    """
    Extrai texto de um PDF usando pdfplumber.
    Fallback para extração por palavras em páginas com fontes customizadas (ex: Serasa).
    """
    pages_text: list[str] = []
    try:
        with pdfplumber.open(pdf_file) as pdf:
            for page in pdf.pages:
                try:
                    text = page.extract_text()
                    if text and text.strip():
                        pages_text.append(text)
                        continue
                except Exception:
                    pass
                try:
                    words = page.extract_words()
                    if words:
                        pages_text.append(" ".join(w["text"] for w in words))
                except Exception:
                    pass
    except Exception as exc:
        st.error(f"Erro ao abrir PDF: {exc}")
        return ""
    return "\n".join(pages_text)


# ── 2. Bureau — Serasa / Neoway ───────────────────────────────────────────────

def extract_serasa_score(text: str) -> Optional[int]:
    """
    Extrai o Score Serasa (0-1000).
    Calibrado ao formato real: "522 80,50% de chance de pagamento"
    """
    if not text:
        return None
    patterns = [
        r"(\d{3})\s+\d{1,3}[,\.]\d{1,2}%\s+de\s+chance\s+de\s+pagamento",
        r"serasa\s+score[\s\S]{0,400}?(\d{3})\b",
        r"score[:\s]+(\d{3,4})\b",
        r"pontua[çc][aã]o[:\s]+(\d{3,4})\b",
        r"\b(\d{3})\s+\d{1,3}[,\.]\d{1,2}%",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            val = int(m.group(1))
            if 0 <= val <= 1000:
                return val
    return None


def extract_neoway_compliance_score(text: str) -> Optional[float]:
    """
    Extrai o Score de Compliance Neoway (0-100).
    Escala: 0 = risco minimo, 100 = risco maximo.
    Suporta inteiro ("Score de Compliance 36") e decimal ("Score de Compliance 19,67").
    """
    if not text:
        return None
    patterns = [
        r"score\s+de\s+compliance\s+([\d]+[,\.][\d]+)",
        r"score\s+de\s+compliance[:\s]+([\d]+[,\.][\d]+)",
        r"compliance\s+score[:\s]+([\d]+[,\.][\d]+)",
        r"score\s+de\s+compliance\s+(\d+)",
        r"score\s+de\s+compliance[:\s]+(\d+)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            val = float(m.group(1).replace(",", "."))
            if 0.0 <= val <= 100.0:
                return val
    return None


def _parse_br_currency(s: str) -> float:
    """Converte 'R$ 360.000,00' -> 360000.0"""
    s = re.sub(r"[R$\s]", "", s)
    s = s.replace(".", "").replace(",", ".")
    return float(s)


def extract_neoway_faturamento(text: str) -> Tuple[Optional[float], Optional[float]]:
    """
    Extrai a faixa de faturamento estimado do relatorio Neoway.
    Calibrado ao formato real: "DE R$ 81.000,01 A R$ 360.000,00"
    Retorna (faturamento_min, faturamento_max).
    """
    if not text:
        return None, None
    patterns = [
        r"de\s+r\$\s*([\d\.]+(?:,\d+)?)\s+a\s+r\$\s*([\d\.]+(?:,\d+)?)",
        r"faturamento\s+estimado[\s\S]{0,120}?r\$\s*([\d\.]+(?:,\d+)?)\s+a\s+r\$\s*([\d\.]+(?:,\d+)?)",
        r"entre\s+r\$\s*([\d\.]+(?:,\d+)?)\s+e\s+r\$\s*([\d\.]+(?:,\d+)?)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            try:
                return _parse_br_currency(m.group(1)), _parse_br_currency(m.group(2))
            except ValueError:
                continue
    return None, None


# ── 3. DRE / Balanco Patrimonial ─────────────────────────────────────────────

def _parse_financial_value(s: str) -> Optional[float]:
    """
    Converte strings financeiras brasileiras para float.
    Suporta: "1.234.567,89" ->  1234567.89
             "(1.234.567)"  -> -1234567.0  (negativo entre parenteses)
             "1.234.567"    ->  1234567.0
             "-1.234.567,89"-> -1234567.89
    """
    s = s.strip()
    negative = s.startswith("(") and s.endswith(")")
    s = re.sub(r"[R$()\s]", "", s)
    if not s:
        return None
    if re.search(r"\d\.\d{3},", s):
        s = s.replace(".", "").replace(",", ".")
    elif re.search(r"\d,\d{2}$", s):
        s = s.replace(",", ".")
    else:
        s = s.replace(".", "").replace(",", "")
    try:
        val = float(s)
        return -val if negative else val
    except ValueError:
        return None


def _extract_conta(text: str, label_patterns: list[str]) -> Optional[float]:
    """
    Extrai um valor numerico associado a um rotulo contabil.
    Busca o padrao: ROTULO <separador> VALOR na mesma linha.
    """
    for label in label_patterns:
        pattern = (
            rf"{label}"
            r"[\s:\.R$]*"
            r"(\(?\s*[\d]{1,3}(?:[.\s]?\d{3})*(?:[.,]\d{{1,2}})?\s*\)?)"
        )
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            val = _parse_financial_value(m.group(1))
            if val is not None and abs(val) > 0:
                return val
    return None


def extract_dre_balanco(text: str) -> Dict[str, Optional[float]]:
    """
    Extrai as contas-chave de um PDF de Balanco Patrimonial / DRE.

    Retorna dict com:
      ativo_circulante, passivo_circulante, estoque, patrimonio_liquido,
      receita_liquida, lucro_liquido, ebitda
    """
    dados: Dict[str, Optional[float]] = {
        "ativo_circulante":   None,
        "passivo_circulante": None,
        "estoque":            None,
        "patrimonio_liquido": None,
        "receita_liquida":    None,
        "lucro_liquido":      None,
        "ebitda":             None,
    }

    if not text:
        return dados

    mapa = {
        "ativo_circulante": [
            r"ativo\s+circulante\s+total",
            r"total\s+do\s+ativo\s+circulante",
            r"ativo\s+circulante",
        ],
        "passivo_circulante": [
            r"passivo\s+circulante\s+total",
            r"total\s+do\s+passivo\s+circulante",
            r"passivo\s+circulante",
        ],
        "estoque": [
            r"estoques?",
            r"mercadorias",
        ],
        "patrimonio_liquido": [
            r"patrim[oô]nio\s+l[ií]quido\s+total",
            r"total\s+do\s+patrim[oô]nio\s+l[ií]quido",
            r"patrim[oô]nio\s+l[ií]quido",
            r"pl\b",
        ],
        "receita_liquida": [
            r"receita\s+l[ií]quida\s+de\s+vendas\s+e\s+servi[çc]os",
            r"receita\s+operacional\s+l[ií]quida",
            r"receita\s+l[ií]quida\s+de\s+vendas",
            r"receita\s+l[ií]quida",
            r"receita\s+de\s+vendas\s+l[ií]quida",
        ],
        "lucro_liquido": [
            r"lucro\s+l[ií]quido\s+do\s+exerc[ií]cio",
            r"lucro\s+l[ií]quido\s+do\s+per[ií]odo",
            r"resultado\s+l[ií]quido\s+do\s+exerc[ií]cio",
            r"lucro\s+l[ií]quido",
        ],
        "ebitda": [
            r"ebitda",
            r"lajida",
            r"lucro\s+antes\s+de\s+juros[,\s]+impostos[,\s]+deprecia[çc][aã]o\s+e\s+amortiza[çc][aã]o",
            r"resultado\s+antes\s+do\s+ir[\s,]+juros[\s,]+deprecia[çc][aã]o",
        ],
    }

    for conta, labels in mapa.items():
        dados[conta] = _extract_conta(text, labels)

    return dados
