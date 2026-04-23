"""
Sistema de Compliance e Risco — Garantidora Premiatto

Uso:
    streamlit run app.py

Dependências:
    pip install streamlit pdfplumber anthropic reportlab pymupdf
"""

import hashlib
import os
import re
import time
import warnings
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Dict, Optional, Tuple

import pdfplumber
import streamlit as st

warnings.filterwarnings("ignore")

# ── Importações opcionais ─────────────────────────────────────────────────────

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.platypus import (
        HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )
    REPORTLAB_OK = True
except ImportError:
    REPORTLAB_OK = False

try:
    from anthropic import Anthropic
    ANTHROPIC_OK = True
except ImportError:
    ANTHROPIC_OK = False

# Carrega a chave Anthropic do st.secrets (local: .streamlit/secrets.toml;
# Cloud: painel Secrets do Streamlit). Fallback para variável de ambiente.
try:
    _secret_key = st.secrets.get("ANTHROPIC_API_KEY", "")
    if _secret_key:
        os.environ["ANTHROPIC_API_KEY"] = _secret_key
except Exception:
    pass


# ═════════════════════════════════════════════════════════════════════════════
# 1. EXTRAÇÃO DE TEXTO DOS PDFs
# ═════════════════════════════════════════════════════════════════════════════

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


# ═════════════════════════════════════════════════════════════════════════════
# 2. EXTRAÇÃO — BUREAU (Serasa / Neoway)
# ═════════════════════════════════════════════════════════════════════════════

def extract_serasa_score(text: str) -> Optional[int]:
    """
    Extrai o Score Serasa (0–1000).
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
    """Extrai o Score de Compliance Neoway.
    Suporta inteiro ("Score de Compliance 36") e decimal ("Score de Compliance 19,67").
    """
    if not text:
        return None
    # Decimal com vírgula ou ponto (ex: 19,67 ou 19.67) — verificado primeiro
    # Depois inteiro puro (ex: 36)
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
    """Converte 'R$ 360.000,00' → 360000.0"""
    s = re.sub(r"[R$\s]", "", s)
    s = s.replace(".", "").replace(",", ".")
    return float(s)


def extract_neoway_faturamento(text: str) -> Tuple[Optional[float], Optional[float]]:
    """Calibrado ao formato real: "DE R$ 81.000,01 A R$ 360.000,00" """
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


# ═════════════════════════════════════════════════════════════════════════════
# 3. EXTRAÇÃO — DRE / BALANÇO PATRIMONIAL
# ═════════════════════════════════════════════════════════════════════════════

def _parse_financial_value(s: str) -> Optional[float]:
    """
    Converte strings financeiras brasileiras para float.
    Suporta: "1.234.567,89"  →  1234567.89
             "(1.234.567)"   → -1234567.0  (negativo entre parênteses)
             "1.234.567"     →  1234567.0
             "-1.234.567,89" → -1234567.89
    """
    s = s.strip()
    negative = s.startswith("(") and s.endswith(")")
    s = re.sub(r"[R$()\s]", "", s)
    if not s:
        return None
    # Detecta formato brasileiro: separador de milhar = ponto, decimal = vírgula
    if re.search(r"\d\.\d{3},", s):          # ex: 1.234.567,89
        s = s.replace(".", "").replace(",", ".")
    elif re.search(r"\d,\d{2}$", s):         # ex: 1234567,89
        s = s.replace(",", ".")
    else:                                      # ex: 1.234.567 (só milhar)
        s = s.replace(".", "").replace(",", "")
    try:
        val = float(s)
        return -val if negative else val
    except ValueError:
        return None


def _extract_conta(text: str, label_patterns: list[str]) -> Optional[float]:
    """
    Extrai um valor numérico associado a um rótulo contábil.
    Busca o padrão: RÓTULO <separador> VALOR na mesma linha.
    """
    for label in label_patterns:
        # Padrão: rótulo seguido (na mesma linha) por um valor monetário
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
    Extrai as contas-chave de um PDF de Balanço Patrimonial / DRE.

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


# ═════════════════════════════════════════════════════════════════════════════
# 3b. AVALIAÇÃO DE BUSINESS PLAN (IA)
# ═════════════════════════════════════════════════════════════════════════════

# Dimensões avaliadas e seus rótulos de exibição
BP_DIMENSOES = {
    "mercado":               "Análise de Mercado",
    "modelo_negocio":        "Modelo de Negócio",
    "projecoes_financeiras": "Projeções Financeiras",
    "equipe_gestao":         "Equipe & Gestão",
    "estrategia_risco":      "Estratégia de Risco",
}


def avaliar_business_plan(
    text: str,
    valor_garantia: float,
    tempo_vigencia: int,
) -> Optional[dict]:
    """
    Envia o texto do Business Plan para a API Anthropic e retorna uma avaliação
    estruturada em JSON com score por dimensão (0–100) e score geral.

    Retorna None se a API não estiver configurada ou o texto for inválido.
    """
    if not ANTHROPIC_OK or not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    if not text or len(text.strip()) < 100:
        return None

    # Limita o texto enviado para evitar exceder o contexto (primeiras ~10k palavras)
    texto_truncado = text[:12000]

    prompt = f"""Você é um analista de crédito sênior especializado em avaliação de planos de negócio para concessão de garantias bancárias.

Analise o Plano de Negócios abaixo considerando que se trata de uma solicitação de garantia de **R$ {valor_garantia:,.2f}** com vigência de **{tempo_vigencia} meses**.

TEXTO DO PLANO DE NEGÓCIOS:
---
{texto_truncado}
---

Avalie cada dimensão com nota de 0 a 100 e uma observação objetiva (1-2 frases). Retorne APENAS um JSON válido — sem nenhum texto antes ou depois:

{{
  "mercado": {{"score": <0-100>, "observacao": "<texto>"}},
  "modelo_negocio": {{"score": <0-100>, "observacao": "<texto>"}},
  "projecoes_financeiras": {{"score": <0-100>, "observacao": "<texto>"}},
  "equipe_gestao": {{"score": <0-100>, "observacao": "<texto>"}},
  "estrategia_risco": {{"score": <0-100>, "observacao": "<texto>"}},
  "score_geral": <0-100>,
  "classificacao": "<Fraco|Regular|Bom|Muito Bom|Excelente>",
  "pontos_fortes": ["<item>", "<item>"],
  "pontos_atencao": ["<item>", "<item>"]
}}

Critérios de avaliação:
- **mercado**: TAM/SAM/SOM, análise competitiva, diferenciação, tendências
- **modelo_negocio**: proposta de valor, fontes de receita, viabilidade, escalabilidade
- **projecoes_financeiras**: consistência das premissas, coerência com o setor, fluxo de caixa
- **equipe_gestao**: experiência, qualificação, complementaridade, track record
- **estrategia_risco**: identificação de riscos, planos de contingência, mitigadores concretos

Se uma dimensão não tiver informações suficientes no documento, atribua score 25 e indique "Informação insuficiente no documento"."""

    try:
        import json
        client = Anthropic()
        resp   = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        # Extrai o bloco JSON mesmo que haja texto residual
        m = re.search(r"\{[\s\S]+\}", raw)
        if m:
            data = json.loads(m.group())
            if "score_geral" in data:
                # Garante que score_geral é numérico
                data["score_geral"] = float(data["score_geral"])
                return data
    except Exception as exc:
        st.warning(f"⚠️ Avaliação do Business Plan: {exc}")
    return None


# ═════════════════════════════════════════════════════════════════════════════
# 4. MOTOR DE SCORE
# ═════════════════════════════════════════════════════════════════════════════

def calculate_payment_capacity(
    faturamento_max: Optional[float],
    valor_garantia: float,
    tempo_vigencia: int,
) -> int:
    """100 se Faturamento_Mensal × Vigência >= Garantia, senão 0."""
    if not faturamento_max or faturamento_max <= 0:
        return 0
    return 100 if (faturamento_max / 12) * tempo_vigencia >= valor_garantia else 0


def calcular_indices_financeiros(
    dados: Dict[str, Optional[float]],
    valor_garantia: float,
) -> Dict[str, Optional[float]]:
    """
    Calcula os índices financeiros e converte cada um numa escala 0–100.

    Sub-índices:
      liquidez_corrente  (peso 40%) — Ideal > 1.2
      cobertura_garantia (peso 40%) — EBITDA / Garantia
      margem_liquida     (peso 20%) — Lucro Líquido / Receita Líquida

    Retorna dict com valores brutos e pontuações normalizadas.
    """
    resumo: Dict[str, Optional[float]] = {}

    # ── Liquidez Corrente ──────────────────────────────────────────────────
    ac = dados.get("ativo_circulante")
    pc = dados.get("passivo_circulante")
    if ac and pc and pc != 0:
        lc = ac / pc
        resumo["liquidez_corrente"] = round(lc, 4)
        if lc >= 2.0:
            resumo["pts_liquidez"] = 100.0
        elif lc >= 1.5:
            resumo["pts_liquidez"] = 80.0
        elif lc >= 1.2:
            resumo["pts_liquidez"] = 60.0
        elif lc >= 0.8:
            resumo["pts_liquidez"] = 30.0
        else:
            resumo["pts_liquidez"] = 10.0
    else:
        resumo["liquidez_corrente"] = None
        resumo["pts_liquidez"] = None

    # ── Cobertura da Garantia (EBITDA / Garantia) ──────────────────────────
    ebitda = dados.get("ebitda")
    if ebitda and valor_garantia > 0:
        cg = ebitda / valor_garantia
        resumo["cobertura_garantia"] = round(cg, 4)
        if cg >= 1.0:
            resumo["pts_cobertura"] = 100.0
        elif cg >= 0.5:
            resumo["pts_cobertura"] = 70.0
        elif cg >= 0.25:
            resumo["pts_cobertura"] = 40.0
        elif cg >= 0.1:
            resumo["pts_cobertura"] = 20.0
        else:
            resumo["pts_cobertura"] = 0.0
    else:
        resumo["cobertura_garantia"] = None
        resumo["pts_cobertura"] = None

    # ── Margem Líquida ─────────────────────────────────────────────────────
    ll = dados.get("lucro_liquido")
    rl = dados.get("receita_liquida")
    if ll is not None and rl and rl != 0:
        ml = ll / rl
        resumo["margem_liquida"] = round(ml, 4)
        if ml >= 0.20:
            resumo["pts_margem"] = 100.0
        elif ml >= 0.10:
            resumo["pts_margem"] = 75.0
        elif ml >= 0.05:
            resumo["pts_margem"] = 50.0
        elif ml >= 0.0:
            resumo["pts_margem"] = 25.0
        else:
            resumo["pts_margem"] = 0.0
    else:
        resumo["margem_liquida"] = None
        resumo["pts_margem"] = None

    # ── Score Financeiro Composto (0–100) ──────────────────────────────────
    # Peso: liquidez 40%, cobertura 40%, margem 20%
    pts_l = resumo.get("pts_liquidez")
    pts_c = resumo.get("pts_cobertura")
    pts_m = resumo.get("pts_margem")

    componentes_disponiveis = [(pts_l, 0.40), (pts_c, 0.40), (pts_m, 0.20)]
    peso_total = sum(w for v, w in componentes_disponiveis if v is not None)

    if peso_total > 0:
        score_fin = sum(v * w for v, w in componentes_disponiveis if v is not None)
        # Reescala pelos pesos disponíveis para não penalizar dados ausentes
        resumo["pontos_financeiro"] = round(score_fin / peso_total, 2)
    else:
        resumo["pontos_financeiro"] = None

    return resumo


def calculate_final_score_standard(
    pontos_serasa: float,
    pontos_neoway: float,
    pontos_capacidade: float,
) -> float:
    """Modo padrão (sem DRE): Serasa 40% | Neoway 30% | Capacidade 30%"""
    return round(
        pontos_serasa     * 0.40
        + pontos_neoway   * 0.30
        + pontos_capacidade * 0.30,
        2,
    )


def calculate_final_score_com_dre(
    pontos_bureau: float,
    pontos_financeiro: float,
    pontos_capacidade: float,
) -> float:
    """Modo DRE: Bureau 30% | Índices Financeiros 40% | Capacidade 30%"""
    return round(
        pontos_bureau       * 0.30
        + pontos_financeiro * 0.40
        + pontos_capacidade * 0.30,
        2,
    )


def calculate_final_score_com_bp(
    pontos_bureau: float,
    pontos_capacidade: float,
    pontos_bp: float,
) -> float:
    """Modo BP (sem DRE): Bureau 35% | Capacidade 25% | Business Plan 40%"""
    return round(
        pontos_bureau       * 0.35
        + pontos_capacidade * 0.25
        + pontos_bp         * 0.40,
        2,
    )


def calculate_final_score_com_dre_bp(
    pontos_bureau: float,
    pontos_financeiro: float,
    pontos_capacidade: float,
    pontos_bp: float,
) -> float:
    """Modo DRE + BP: Bureau 20% | Financeiro 30% | Capacidade 20% | BP 30%"""
    return round(
        pontos_bureau       * 0.20
        + pontos_financeiro * 0.30
        + pontos_capacidade * 0.20
        + pontos_bp         * 0.30,
        2,
    )


def get_risk_classification(score: float) -> Tuple[str, str, str]:
    """Retorna (label, hex_color, emoji)."""
    if score < 40:
        return "Risco Alto",          "#E53E3E", "🔴"
    elif score < 70:
        return "Risco Moderado-Alto", "#DD6B20", "🟠"
    elif score < 90:
        return "Risco Moderado",      "#D69E2E", "🟡"
    else:
        return "Risco Mínimo",        "#38A169", "🟢"


# ═════════════════════════════════════════════════════════════════════════════
# 5. GERAÇÃO DE PARECER TÉCNICO (IA — Anthropic)
# ═════════════════════════════════════════════════════════════════════════════

def generate_technical_opinion(
    data: dict,
    indices: Optional[dict] = None,
    avaliacao_bp: Optional[dict] = None,
    finalidade_text: Optional[str] = None,
) -> str:
    if not ANTHROPIC_OK:
        return "❌ Biblioteca `anthropic` não instalada. Execute: `pip install anthropic`"
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return (
            "❌ Chave ANTHROPIC_API_KEY não configurada.\n"
            "Insira-a no campo da barra lateral ou defina a variável de ambiente."
        )

    fat_min   = data.get("faturamento_min")
    fat_max   = data.get("faturamento_max")
    fat_str   = f"R$ {fat_min:,.2f} a R$ {fat_max:,.2f}" if fat_min and fat_max else "Não disponível"
    fat_m     = (fat_max or 0) / 12
    cap       = fat_m * data.get("tempo_vigencia", 0)
    modo_dre  = data.get("modo_dre", False)
    modo_bp   = avaliacao_bp is not None

    # Rótulo de risco do score Neoway para contextualizar a IA
    _neo_orig = data.get('score_neoway_original')
    if _neo_orig is not None:
        if _neo_orig <= 30:
            _neo_label = "RISCO BAIXO"
        elif _neo_orig <= 50:
            _neo_label = "RISCO MODERADO"
        elif _neo_orig <= 70:
            _neo_label = "RISCO ALTO"
        else:
            _neo_label = "RISCO MUITO ALTO"
        _neo_str = f"{_neo_orig} / 100 → {_neo_label}"
    else:
        _neo_str = "N/D"

    # Bloco bureau
    if modo_dre:
        bureau_bloco = f"""\
BUREAU — SERASA + NEOWAY (Peso 30%)
  Score Serasa PF              : {data.get('score_serasa_pf', 'N/D')} / 1000  [escala: 0=alto risco → 1000=risco mínimo]
  Score Serasa PJ              : {data.get('score_serasa_pj', 'N/D')} / 1000  [escala: 0=alto risco → 1000=risco mínimo]
  Score Compliance Neoway      : {_neo_str}  [escala: 0=risco mínimo → 100=risco máximo]
  Pontos Bureau (combinado)    : {data.get('pontos_bureau', 0):.1f} / 100
  Contribuição ao score        : {data.get('componente_bureau', 0):.2f} pts"""
    else:
        bureau_bloco = f"""\
SAÚDE FINANCEIRA (Peso 40%)
  Score Serasa PF              : {data.get('score_serasa_pf', 'N/D')} / 1000  [escala: 0=alto risco → 1000=risco mínimo]
  Score Serasa PJ              : {data.get('score_serasa_pj', 'N/D')} / 1000  [escala: 0=alto risco → 1000=risco mínimo]
  Pontos normalizados          : {data.get('pontos_serasa', 0):.1f} / 100
  Contribuição ao score        : {data.get('componente_saude', 0):.2f} pts

COMPLIANCE (Peso 30%)
  Score Compliance Neoway      : {_neo_str}  [escala: 0=risco mínimo → 100=risco máximo]
  Pontos normalizados (inv.)   : {data.get('pontos_neoway', 0):.1f} / 100  [invertido: 100-score → maior pts = melhor]
  Contribuição ao score        : {data.get('componente_compliance', 0):.2f} pts"""

    # Bloco DRE (se disponível)
    if modo_dre and indices:
        def fmt(v): return f"{v:,.2f}" if v is not None else "N/D"
        def fmtp(v): return f"{v:.1f}" if v is not None else "N/D"

        lc_raw = indices.get("liquidez_corrente")
        cg_raw = indices.get("cobertura_garantia")
        ml_raw = indices.get("margem_liquida")

        dre_bloco = f"""\

ANÁLISE FINANCEIRA — DRE / BALANÇO (Peso 40%)
  Ativo Circulante             : R$ {fmt(data.get('ativo_circulante'))}
  Passivo Circulante           : R$ {fmt(data.get('passivo_circulante'))}
  Estoque                      : R$ {fmt(data.get('estoque'))}
  Patrimônio Líquido           : R$ {fmt(data.get('patrimonio_liquido'))}
  Receita Líquida              : R$ {fmt(data.get('receita_liquida'))}
  Lucro Líquido                : R$ {fmt(data.get('lucro_liquido'))}
  EBITDA                       : R$ {fmt(data.get('ebitda'))}

  Liquidez Corrente            : {fmt(lc_raw)}x  → {fmtp(indices.get('pts_liquidez'))} pts  (ideal > 1,2)
  Cobertura da Garantia (EBITDA): {fmt(cg_raw)}x  → {fmtp(indices.get('pts_cobertura'))} pts
  Margem Líquida               : {f'{lc_raw*100:.1f}%' if ml_raw is None else f'{ml_raw*100:.1f}%'}  → {fmtp(indices.get('pts_margem'))} pts
  Pontos Financeiros (total)   : {fmtp(indices.get('pontos_financeiro'))} / 100
  Contribuição ao score        : {data.get('componente_financeiro', 0):.2f} pts"""
    else:
        dre_bloco = ""

    if finalidade_text:
        _trunc = finalidade_text[:3000] + ("...[truncado]" if len(finalidade_text) > 3000 else "")
        _finalidade_bloco = (
            "\n---\nFINALIDADE DO USO DO RECURSO (contexto qualitativo)\n"
            "---\n" + _trunc + "\n---\n"
        )
    else:
        _finalidade_bloco = ""

    prompt = f"""Você é um analista de risco sênior de uma garantidora brasileira.
Com base exclusivamente nos dados abaixo, redija um PARECER TÉCNICO formal em português.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DADOS DA ANÁLISE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Valor da Garantia Solicitada : R$ {data.get('valor_garantia', 0):,.2f}
Tempo de Vigência            : {data.get('tempo_vigencia', 0)} meses
Metodologia                  : {'DRE + Business Plan' if (modo_dre and modo_bp) else 'Business Plan' if modo_bp else 'Com DRE/Balanço' if modo_dre else 'Padrão'}

{bureau_bloco}{dre_bloco}
{f'''
BUSINESS PLAN (Peso {'30%' if modo_dre else '40%'})
  Score Geral                  : {avaliacao_bp.get("score_geral", 0):.1f} / 100
  Classificação BP             : {avaliacao_bp.get("classificacao", "N/D")}
  Análise de Mercado           : {avaliacao_bp.get("mercado", {}).get("score", "N/D")} pts — {avaliacao_bp.get("mercado", {}).get("observacao", "")}
  Modelo de Negócio            : {avaliacao_bp.get("modelo_negocio", {}).get("score", "N/D")} pts — {avaliacao_bp.get("modelo_negocio", {}).get("observacao", "")}
  Projeções Financeiras        : {avaliacao_bp.get("projecoes_financeiras", {}).get("score", "N/D")} pts — {avaliacao_bp.get("projecoes_financeiras", {}).get("observacao", "")}
  Equipe & Gestão              : {avaliacao_bp.get("equipe_gestao", {}).get("score", "N/D")} pts — {avaliacao_bp.get("equipe_gestao", {}).get("observacao", "")}
  Estratégia de Risco          : {avaliacao_bp.get("estrategia_risco", {}).get("score", "N/D")} pts — {avaliacao_bp.get("estrategia_risco", {}).get("observacao", "")}
  Pontos Fortes                : {"; ".join(avaliacao_bp.get("pontos_fortes", []))}
  Pontos de Atenção            : {"; ".join(avaliacao_bp.get("pontos_atencao", []))}
  Contribuição ao score        : {data.get("componente_bp", 0):.2f} pts''' if modo_bp else ''}

CAPACIDADE DE PAGAMENTO (Peso 30%)
  Faturamento Estimado         : {fat_str}
  Faturamento Mensal           : R$ {fat_m:,.2f}
  Capacidade no Período        : R$ {cap:,.2f}
  Pontos                       : {data.get('pontos_capacidade', 0)} / 100
  Contribuição ao score        : {data.get('componente_capacidade', 0):.2f} pts

RESULTADO FINAL
  Score Final                  : {data.get('score_final', 0):.2f} / 100
  Classificação                : {data.get('classificacao', 'N/D')}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{_finalidade_bloco}
CONVENCAO DAS ESCALAS - LEIA ANTES DE REDIGIR
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Serasa (PF e PJ): escala 0–1000. Quanto MAIOR o score, MENOR o risco de inadimplência.
  Ex.: score 800 = excelente; score 200 = alto risco.
• Neoway Score de Compliance: escala 0–100. Quanto MENOR o score, MENOR o risco (melhor compliance).
  Valores baixos (ex.: 36) indicam empresa com BAIXO risco de compliance.
  Valores altos (ex.: 85) indicam empresa com ALTO risco de compliance.
  O sistema INVERTE esse score internamente (100 − score) para normalizar: score 36 → 64 pts.
• Score final da análise: escala 0–100. Quanto MAIOR, MENOR o risco. 0 = risco máximo; 100 = risco mínimo.
• Pontos normalizados (qualquer pilar): SEMPRE interpretados como maior = melhor.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Estruture o parecer com os seguintes tópicos obrigatórios:
1. RESUMO EXECUTIVO
2. ANÁLISE DE SAÚDE FINANCEIRA / BUREAU
{'3. ANÁLISE DOS ÍNDICES FINANCEIROS (DRE/Balanço)' if modo_dre else '3. ANÁLISE DE COMPLIANCE'}
{'4. ANÁLISE DO BUSINESS PLAN' if modo_bp else ''}
{'5' if modo_bp else '4'}. ANÁLISE DE CAPACIDADE DE PAGAMENTO
{'6' if modo_bp else '5'}. FATORES DE RISCO IDENTIFICADOS
{'7' if modo_bp else '6'}. RECOMENDAÇÃO (Aprovar / Aprovar com Condicionantes / Recusar)
{'8' if modo_bp else '7'}. CONDICIONANTES E MITIGANTES (se aplicável)

Seja objetivo, técnico e fundamentado apenas nos dados fornecidos."""

    try:
        client = Anthropic()
        resp   = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text
    except Exception as exc:
        return f"Erro ao gerar parecer: {exc}"


# ═════════════════════════════════════════════════════════════════════════════
# 6. GERAÇÃO DE RELATÓRIO PARA DOWNLOAD
# ═════════════════════════════════════════════════════════════════════════════

def generate_txt_report(
    data: dict,
    parecer: str,
    indices: Optional[dict] = None,
    avaliacao_bp: Optional[dict] = None,
    finalidade_text: Optional[str] = None,
) -> str:
    ts      = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    fat_min = data.get("faturamento_min") or 0
    fat_max = data.get("faturamento_max") or 0
    fat_m   = fat_max / 12
    cap     = fat_m * data.get("tempo_vigencia", 0)
    modo      = data.get("modo_dre", False)
    modo_bp_r = avaliacao_bp is not None

    def fv(v): return f"{v:,.2f}" if v is not None else "Não extraído"

    # Bloco de composição do score
    if modo and modo_bp_r:
        comp_bloco = f"""\
COMPOSIÇÃO DO SCORE FINAL (Metodologia DRE + Business Plan)
  Bureau  (Serasa+Neoway) (20%) : {data.get('componente_bureau', 0):>6.2f} pts
  Índices Financeiros     (30%) : {data.get('componente_financeiro', 0):>6.2f} pts
  Cap. de Pagamento       (20%) : {data.get('componente_capacidade', 0):>6.2f} pts
  Business Plan           (30%) : {data.get('componente_bp', 0):>6.2f} pts"""
    elif modo_bp_r:
        comp_bloco = f"""\
COMPOSIÇÃO DO SCORE FINAL (Metodologia com Business Plan)
  Bureau  (Serasa+Neoway) (35%) : {data.get('componente_bureau', 0):>6.2f} pts
  Cap. de Pagamento       (25%) : {data.get('componente_capacidade', 0):>6.2f} pts
  Business Plan           (40%) : {data.get('componente_bp', 0):>6.2f} pts"""
    elif modo:
        comp_bloco = f"""\
COMPOSIÇÃO DO SCORE FINAL (Metodologia com DRE)
  Bureau  (Serasa+Neoway) (30%) : {data.get('componente_bureau', 0):>6.2f} pts
  Índices Financeiros     (40%) : {data.get('componente_financeiro', 0):>6.2f} pts
  Cap. de Pagamento       (30%) : {data.get('componente_capacidade', 0):>6.2f} pts"""
    else:
        comp_bloco = f"""\
COMPOSIÇÃO DO SCORE FINAL (Metodologia Padrão)
  Saúde Financeira (Serasa)(40%): {data.get('componente_saude', 0):>6.2f} pts
  Compliance (Neoway)      (30%): {data.get('componente_compliance', 0):>6.2f} pts
  Cap. de Pagamento        (30%): {data.get('componente_capacidade', 0):>6.2f} pts"""

    # Bloco DRE
    dre_bloco = ""
    if modo and indices:
        lc = indices.get("liquidez_corrente")
        cg = indices.get("cobertura_garantia")
        ml = indices.get("margem_liquida")
        dre_bloco = f"""
ÍNDICES FINANCEIROS (DRE / BALANÇO)
  Ativo Circulante             : R$ {fv(data.get('ativo_circulante'))}
  Passivo Circulante           : R$ {fv(data.get('passivo_circulante'))}
  Estoque                      : R$ {fv(data.get('estoque'))}
  Patrimônio Líquido           : R$ {fv(data.get('patrimonio_liquido'))}
  Receita Líquida              : R$ {fv(data.get('receita_liquida'))}
  Lucro Líquido                : R$ {fv(data.get('lucro_liquido'))}
  EBITDA                       : R$ {fv(data.get('ebitda'))}

  Liquidez Corrente            : {f'{lc:.4f}x' if lc else 'N/D':>10}  ({f'{indices.get("pts_liquidez", 0):.1f}' if indices.get("pts_liquidez") is not None else 'N/D'} pts)
  Cobertura da Garantia        : {f'{cg:.4f}x' if cg else 'N/D':>10}  ({f'{indices.get("pts_cobertura", 0):.1f}' if indices.get("pts_cobertura") is not None else 'N/D'} pts)
  Margem Líquida               : {f'{ml*100:.2f}%' if ml is not None else 'N/D':>10}  ({f'{indices.get("pts_margem", 0):.1f}' if indices.get("pts_margem") is not None else 'N/D'} pts)
  Pontos Financeiros (total)   : {f'{indices.get("pontos_financeiro", 0):.2f}' if indices.get("pontos_financeiro") is not None else 'N/D':>10} / 100
"""

    # Bloco Business Plan (pre-calculado para evitar f-string aninhado)
    if modo_bp_r and avaliacao_bp:
        _bp_fortes  = "\n".join("  - " + p for p in avaliacao_bp.get("pontos_fortes",  [])) or "  (nenhum)"
        _bp_atencao = "\n".join("  - " + p for p in avaliacao_bp.get("pontos_atencao", [])) or "  (nenhum)"
        _bp_geral   = avaliacao_bp.get("score_geral", 0)
        _bp_class   = avaliacao_bp.get("classificacao", "N/D")
        _bp_mercado = avaliacao_bp.get("mercado", {}).get("score", "N/D")
        _bp_modelo  = avaliacao_bp.get("modelo_negocio", {}).get("score", "N/D")
        _bp_proj    = avaliacao_bp.get("projecoes_financeiras", {}).get("score", "N/D")
        _bp_equipe  = avaliacao_bp.get("equipe_gestao", {}).get("score", "N/D")
        _bp_risco   = avaliacao_bp.get("estrategia_risco", {}).get("score", "N/D")
        bp_bloco = f"""
AVALIAÇÃO DO BUSINESS PLAN
  Score Geral                  : {_bp_geral:.1f} / 100
  Classificação                : {_bp_class}
  Análise de Mercado           : {_bp_mercado} pts
  Modelo de Negócio            : {_bp_modelo} pts
  Projeções Financeiras        : {_bp_proj} pts
  Equipe & Gestão              : {_bp_equipe} pts
  Estratégia de Risco          : {_bp_risco} pts
  Pontos Fortes:
{_bp_fortes}
  Pontos de Atenção:
{_bp_atencao}
"""
    else:
        bp_bloco = ""

    _metodo_label = (
        "COM DRE + Business Plan" if (modo and modo_bp_r) else
        "COM Business Plan"       if modo_bp_r else
        "COM Análise Financeira (DRE/Balanço)" if modo else
        "Padrão (Bureau + Capacidade)"
    )

    if finalidade_text:
        _trunc_f = finalidade_text[:2000] + ("...[texto truncado]" if len(finalidade_text) > 2000 else "")
        _fin_bloco_txt = "\nFINALIDADE DO USO DO RECURSO\n" + _trunc_f + "\n"
    else:
        _fin_bloco_txt = ""

    return f"""\
================================================================================
         RELATÓRIO DE ANÁLISE DE RISCO — GARANTIDORA
================================================================================
Emitido em: {ts}
Metodologia: {_metodo_label}

DADOS DA SOLICITAÇÃO
  Valor da Garantia Solicitada : R$ {data.get('valor_garantia', 0):>14,.2f}
  Tempo de Vigência            : {data.get('tempo_vigencia', 0)} meses

BUREAU — SCORES EXTRAÍDOS
  Score Serasa PF              : {str(data.get('score_serasa_pf', 'Não extraído')):>6}  / 1000
  Score Serasa PJ              : {str(data.get('score_serasa_pj', 'Não extraído')):>6}  / 1000
  Score Compliance Neoway (orig): {str(data.get('score_neoway_original', 'Não extraído')):>6}  / 100
  Faturamento Estimado         : R$ {fat_min:,.2f} a R$ {fat_max:,.2f}
{dre_bloco}{bp_bloco}{_fin_bloco_txt}
CAPACIDADE DE PAGAMENTO
  Faturamento Mensal           : R$ {fat_m:,.2f}
  Capacidade Total no Período  : R$ {cap:,.2f}
  Valor da Garantia            : R$ {data.get('valor_garantia', 0):,.2f}
  Situação                     : {'Suficiente' if cap >= data.get('valor_garantia', 0) else 'Insuficiente'}

{comp_bloco}
  ─────────────────────────────────────
  SCORE FINAL                  : {data.get('score_final', 0):>6.2f} / 100
  CLASSIFICAÇÃO                : {data.get('classificacao', 'N/D')}

================================================================================
 PARECER TÉCNICO (GERADO POR IA — Anthropic claude-opus-4-6)
================================================================================

{parecer}

================================================================================
 Documento gerado automaticamente — Sistema de Compliance e Risco — Garantidora Premiatto
================================================================================
"""


# ── Markdown → ReportLab ─────────────────────────────────────────────────────

def _md_to_xml(text: str) -> str:
    """
    Converte markdown inline para XML compatível com reportlab Paragraph.
    Sequência obrigatória: escape HTML → bold → italic.
    """
    # 1. Escapa caracteres especiais XML
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # 2. **negrito** → <b>negrito</b>
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.DOTALL)
    # 3. *itálico* → <i>itálico</i>  (evita capturar ** residuais)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", text)
    # 4. _itálico_
    text = re.sub(r"\b_(.+?)_\b", r"<i>\1</i>", text)
    return text


def _parecer_to_flowables(
    parecer: str,
    body_s,
    h2_s,
    h3_s,
    navy,
    teal,
    bgGray,
) -> list:
    """
    Converte o parecer em markdown para uma lista de flowables do reportlab.
    Suporta: # ## ### headings | **bold** *italic* | listas - | tabelas | | --- separadores.
    """
    bullet_s = ParagraphStyle("BulletParecer", parent=body_s,
                               leftIndent=14, spaceAfter=2, fontSize=9)
    h1_p     = ParagraphStyle("H1Parecer",    parent=h2_s,
                               fontSize=13, spaceBefore=8, spaceAfter=4)

    flowables = []
    lines     = parecer.split("\n")
    i         = 0

    while i < len(lines):
        stripped = lines[i].strip()

        # Linha vazia
        if not stripped:
            flowables.append(Spacer(1, 5))
            i += 1
            continue

        # Separador ---
        if re.match(r"^-{3,}$", stripped):
            flowables += [
                Spacer(1, 4),
                HRFlowable(width="100%", thickness=0.4, color=colors.lightgrey),
                Spacer(1, 4),
            ]
            i += 1
            continue

        # Headings
        if stripped.startswith("### "):
            flowables.append(Paragraph(_md_to_xml(stripped[4:]), h3_s))
            i += 1
            continue
        if stripped.startswith("## "):
            flowables += [Spacer(1, 6), Paragraph(_md_to_xml(stripped[3:]), h2_s)]
            i += 1
            continue
        if stripped.startswith("# "):
            flowables += [Spacer(1, 8), Paragraph(_md_to_xml(stripped[2:]), h1_p)]
            i += 1
            continue

        # Tabela markdown  |col|col|
        if stripped.startswith("|"):
            table_lines: list[str] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i].strip())
                i += 1

            # Filtra linha separadora |---|---|
            data_rows: list[list[str]] = []
            for tl in table_lines:
                if re.match(r"^\|[\s\-:|]+\|$", tl):
                    continue
                cells = [c.strip() for c in tl.strip("|").split("|")]
                data_rows.append(cells)

            if data_rows:
                ncols     = max(len(r) for r in data_rows)
                col_width = 440 / ncols
                # Normaliza colunas
                for r in data_rows:
                    while len(r) < ncols:
                        r.append("")

                para_rows = []
                for ri, row in enumerate(data_rows):
                    cell_style = ParagraphStyle(
                        f"TCell_{ri}",
                        parent=body_s,
                        fontSize=8,
                        fontName="Helvetica-Bold" if ri == 0 else "Helvetica",
                    )
                    para_rows.append(
                        [Paragraph(_md_to_xml(c), cell_style) for c in row]
                    )

                tbl = Table(para_rows, colWidths=[col_width] * ncols)
                tbl.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1,  0), teal),
                    ("TEXTCOLOR",  (0, 0), (-1,  0), colors.white),
                    ("BACKGROUND", (0, 1), (-1, -1), bgGray),
                    ("GRID",       (0, 0), (-1, -1), 0.35, colors.lightgrey),
                    ("PADDING",    (0, 0), (-1, -1), 5),
                    ("VALIGN",     (0, 0), (-1, -1), "TOP"),
                ]))
                flowables += [tbl, Spacer(1, 6)]
            continue

        # Bullet list  - item  ou  * item
        if re.match(r"^[-\*]\s", stripped):
            flowables.append(
                Paragraph(f"&bull; {_md_to_xml(stripped[2:])}", bullet_s)
            )
            i += 1
            continue

        # Parágrafo normal
        flowables.append(Paragraph(_md_to_xml(stripped), body_s))
        i += 1

    return flowables


def generate_pdf_report(
    data: dict,
    parecer: str,
    indices: Optional[dict] = None,
    avaliacao_bp: Optional[dict] = None,
    finalidade_text: Optional[str] = None,
) -> Optional[BytesIO]:
    if not REPORTLAB_OK:
        return None

    buffer = BytesIO()
    doc    = SimpleDocTemplate(
        buffer, pagesize=A4,
        rightMargin=55, leftMargin=55,
        topMargin=55,   bottomMargin=55,
    )
    styles = getSampleStyleSheet()
    navy   = colors.HexColor("#1a1a2e")
    teal   = colors.HexColor("#0f3460")
    bgGray = colors.HexColor("#f5f5f5")
    bgBlue = colors.HexColor("#e8f0fe")
    modo   = data.get("modo_dre", False)

    title_s = ParagraphStyle("T2",    parent=styles["Title"],   textColor=navy, fontSize=15, spaceAfter=4)
    h2_s    = ParagraphStyle("H2_",   parent=styles["Heading2"], textColor=navy, fontSize=11, spaceAfter=4)
    h3_s    = ParagraphStyle("H3_",   parent=styles["Heading3"], textColor=teal, fontSize=10, spaceAfter=2)
    small_s = ParagraphStyle("Sm_",   parent=styles["Normal"],   textColor=colors.gray, fontSize=8)
    body_s  = styles["Normal"]

    def two_col(rows, w1=205, w2=235):
        t = Table(rows, colWidths=[w1, w2])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, -1), bgGray),
            ("FONTNAME",   (0, 0), (0, -1), "Helvetica-Bold"),
            ("GRID",       (0, 0), (-1,-1), 0.35, colors.lightgrey),
            ("PADDING",    (0, 0), (-1,-1), 6),
        ]))
        return t

    story = [
        Paragraph("RELATÓRIO DE ANÁLISE DE RISCO", title_s),
        Paragraph("Sistema de Compliance — Garantidora Premiatto", small_s),
        Paragraph(
            f"Emitido em: {datetime.now().strftime('%d/%m/%Y %H:%M')} · "
            f"Metodologia: {'Com DRE/Balanço' if modo else 'Padrão'}",
            small_s,
        ),
        Spacer(1, 12),
    ]

    # Dados solicitação
    fat_min = data.get("faturamento_min") or 0
    fat_max = data.get("faturamento_max") or 0
    story += [
        Paragraph("Dados da Solicitação", h2_s),
        two_col([
            ["Valor da Garantia", f"R$ {data.get('valor_garantia', 0):,.2f}"],
            ["Tempo de Vigência", f"{data.get('tempo_vigencia', 0)} meses"],
        ]),
        Spacer(1, 10),
    ]

    # Bureau
    story += [
        Paragraph("Bureau — Scores Extraídos", h2_s),
        two_col([
            ["Score Serasa PF",               str(data.get("score_serasa_pf", "N/D"))],
            ["Score Serasa PJ",               str(data.get("score_serasa_pj", "N/D"))],
            ["Score Compliance Neoway (orig)", str(data.get("score_neoway_original", "N/D"))],
            ["Faturamento Mín.",              f"R$ {fat_min:,.2f}"],
            ["Faturamento Máx.",              f"R$ {fat_max:,.2f}"],
        ]),
        Spacer(1, 10),
    ]

    # Índices financeiros (DRE)
    if modo and indices:
        def fv(v): return f"R$ {v:,.2f}" if v is not None else "N/D"
        def fi(v): return f"{v:.1f} pts" if v is not None else "N/D"
        lc = indices.get("liquidez_corrente")
        cg = indices.get("cobertura_garantia")
        ml = indices.get("margem_liquida")

        story += [
            Paragraph("Análise Financeira — DRE / Balanço", h2_s),
            Paragraph("Contas Extraídas", h3_s),
            two_col([
                ["Ativo Circulante",   fv(data.get("ativo_circulante"))],
                ["Passivo Circulante", fv(data.get("passivo_circulante"))],
                ["Estoque",            fv(data.get("estoque"))],
                ["Patrimônio Líquido", fv(data.get("patrimonio_liquido"))],
                ["Receita Líquida",    fv(data.get("receita_liquida"))],
                ["Lucro Líquido",      fv(data.get("lucro_liquido"))],
                ["EBITDA",             fv(data.get("ebitda"))],
            ]),
            Spacer(1, 6),
            Paragraph("Índices Calculados", h3_s),
        ]

        idx_rows = [
            ["Índice", "Valor Calculado", "Pts (0–100)", "Referência"],
            ["Liquidez Corrente (40%)",
             f"{lc:.4f}x" if lc else "N/D",
             fi(indices.get("pts_liquidez")),
             "Ideal > 1,2"],
            ["Cobertura Garantia (40%)",
             f"{cg:.4f}x" if cg else "N/D",
             fi(indices.get("pts_cobertura")),
             "EBITDA / Garantia"],
            ["Margem Líquida (20%)",
             f"{ml*100:.2f}%" if ml is not None else "N/D",
             fi(indices.get("pts_margem")),
             "Lucro / Receita"],
            ["SCORE FINANCEIRO", "",
             f"{indices.get('pontos_financeiro', 0):.2f}" if indices.get('pontos_financeiro') is not None else "N/D",
             ""],
        ]
        it = Table(idx_rows, colWidths=[155, 90, 80, 105])
        it.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), teal),
            ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
            ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME",   (0, 4), (-1, 4), "Helvetica-Bold"),
            ("BACKGROUND", (0, 4), (-1, 4), bgBlue),
            ("GRID",       (0, 0), (-1,-1), 0.35, colors.lightgrey),
            ("ALIGN",      (1, 0), (-1,-1), "CENTER"),
            ("PADDING",    (0, 0), (-1,-1), 6),
        ]))
        story += [it, Spacer(1, 10)]

    # Finalidade do Recurso
    if finalidade_text:
        story += [
            Paragraph("Finalidade do Uso do Recurso", h2_s),
            Spacer(1, 4),
        ]
        for linha in finalidade_text[:3000].split("\n"):
            linha = linha.strip()
            if linha:
                story.append(Paragraph(_md_to_xml(linha), body_s))
        story.append(Spacer(1, 10))

    # Business Plan
    if avaliacao_bp:
        def fi(v): return f"{v:.0f} pts" if v is not None else "N/D"
        story += [
            Paragraph("Avaliação do Business Plan", h2_s),
            Spacer(1, 4),
        ]
        # Score geral em destaque
        bp_score  = avaliacao_bp.get("score_geral", 0)
        bp_class  = avaliacao_bp.get("classificacao", "")
        bp_color  = (colors.HexColor("#E53E3E") if bp_score < 40
                     else colors.HexColor("#DD6B20") if bp_score < 60
                     else colors.HexColor("#D69E2E") if bp_score < 75
                     else colors.HexColor("#38A169"))
        bp_res_s  = ParagraphStyle("BPRes", parent=styles["Heading2"],
                                    fontSize=14, textColor=bp_color, spaceAfter=6)
        story.append(
            Paragraph(f"Score Geral: {bp_score:.1f} / 100 — {bp_class}", bp_res_s)
        )

        # Tabela de dimensões
        dim_rows = [["Dimensão", "Score", "Observação"]]
        for chave, rotulo in BP_DIMENSOES.items():
            dim = avaliacao_bp.get(chave, {})
            dim_rows.append([
                Paragraph(rotulo, ParagraphStyle("dc", parent=body_s, fontSize=8)),
                Paragraph(str(dim.get("score", "N/D")), ParagraphStyle("ds", parent=body_s, fontSize=8, alignment=1)),
                Paragraph(_md_to_xml(dim.get("observacao", "")), ParagraphStyle("do", parent=body_s, fontSize=8)),
            ])

        dt = Table(dim_rows, colWidths=[115, 45, 280])
        dt.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), teal),
            ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
            ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID",       (0, 0), (-1,-1), 0.35, colors.lightgrey),
            ("BACKGROUND", (0, 1), (-1,-1), bgGray),
            ("PADDING",    (0, 0), (-1,-1), 5),
            ("VALIGN",     (0, 0), (-1,-1), "TOP"),
        ]))
        story += [dt, Spacer(1, 6)]

        # Pontos fortes / atenção
        pf_list = avaliacao_bp.get("pontos_fortes", [])
        pa_list = avaliacao_bp.get("pontos_atencao", [])
        bullet_bp = ParagraphStyle("BPBullet", parent=body_s, fontSize=8, leftIndent=10)
        if pf_list:
            story.append(Paragraph("<b>Pontos Fortes:</b>", body_s))
            for p in pf_list:
                story.append(Paragraph(f"&bull; {_md_to_xml(p)}", bullet_bp))
        if pa_list:
            story.append(Paragraph("<b>Pontos de Atenção:</b>", body_s))
            for p in pa_list:
                story.append(Paragraph(f"&bull; {_md_to_xml(p)}", bullet_bp))
        story.append(Spacer(1, 10))

    # Composição do score
    story.append(Paragraph("Composição do Score Final", h2_s))
    bp_pts_str = f"{avaliacao_bp.get('score_geral', 0):.1f}" if avaliacao_bp else "N/D"
    fin_pts_str = (f"{indices.get('pontos_financeiro', 0):.1f}"
                   if indices and indices.get("pontos_financeiro") is not None else "N/D")

    if modo and avaliacao_bp:
        comp_rows = [
            ["Componente", "Pts Brutos", "Peso", "Contribuição"],
            ["Bureau (Serasa + Neoway)", f"{data.get('pontos_bureau', 0):.1f}", "20%", f"{data.get('componente_bureau', 0):.2f}"],
            ["Índices Financeiros (DRE)", fin_pts_str,                           "30%", f"{data.get('componente_financeiro', 0):.2f}"],
            ["Capacidade de Pagamento",  str(data.get("pontos_capacidade", 0)),  "20%", f"{data.get('componente_capacidade', 0):.2f}"],
            ["Business Plan (IA)",       bp_pts_str,                             "30%", f"{data.get('componente_bp', 0):.2f}"],
            ["SCORE FINAL", "", "", f"{data.get('score_final', 0):.2f}"],
        ]
    elif avaliacao_bp:
        comp_rows = [
            ["Componente", "Pts Brutos", "Peso", "Contribuição"],
            ["Bureau (Serasa + Neoway)", f"{data.get('pontos_bureau', 0):.1f}", "35%", f"{data.get('componente_bureau', 0):.2f}"],
            ["Capacidade de Pagamento",  str(data.get("pontos_capacidade", 0)),  "25%", f"{data.get('componente_capacidade', 0):.2f}"],
            ["Business Plan (IA)",       bp_pts_str,                             "40%", f"{data.get('componente_bp', 0):.2f}"],
            ["SCORE FINAL", "", "", f"{data.get('score_final', 0):.2f}"],
        ]
    elif modo:
        comp_rows = [
            ["Componente", "Pts Brutos", "Peso", "Contribuição"],
            ["Bureau (Serasa + Neoway)", f"{data.get('pontos_bureau', 0):.1f}", "30%", f"{data.get('componente_bureau', 0):.2f}"],
            ["Índices Financeiros (DRE)", fin_pts_str,                           "40%", f"{data.get('componente_financeiro', 0):.2f}"],
            ["Capacidade de Pagamento",  str(data.get("pontos_capacidade", 0)),  "30%", f"{data.get('componente_capacidade', 0):.2f}"],
            ["SCORE FINAL", "", "", f"{data.get('score_final', 0):.2f}"],
        ]
    else:
        comp_rows = [
            ["Componente", "Pts Brutos", "Peso", "Contribuição"],
            ["Saúde Financeira (Serasa)", f"{data.get('pontos_serasa', 0):.1f}", "40%", f"{data.get('componente_saude', 0):.2f}"],
            ["Compliance (Neoway inv.)",  f"{data.get('pontos_neoway', 0):.1f}", "30%", f"{data.get('componente_compliance', 0):.2f}"],
            ["Capacidade de Pagamento",   str(data.get("pontos_capacidade", 0)), "30%", f"{data.get('componente_capacidade', 0):.2f}"],
            ["SCORE FINAL", "", "", f"{data.get('score_final', 0):.2f}"],
        ]

    ct = Table(comp_rows, colWidths=[168, 75, 55, 100])
    ct.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1,  0), navy),
        ("TEXTCOLOR",  (0, 0), (-1,  0), colors.white),
        ("FONTNAME",   (0, 0), (-1,  0), "Helvetica-Bold"),
        ("FONTNAME",   (0, 4), (-1,  4), "Helvetica-Bold"),
        ("BACKGROUND", (0, 4), (-1,  4), colors.HexColor("#d4edda")),
        ("GRID",       (0, 0), (-1, -1), 0.35, colors.lightgrey),
        ("ALIGN",      (1, 0), (-1, -1), "CENTER"),
        ("PADDING",    (0, 0), (-1, -1), 6),
    ]))
    story += [ct, Spacer(1, 12)]

    # Resultado
    score = data.get("score_final", 0)
    rc = (colors.HexColor("#E53E3E") if score < 40
          else colors.HexColor("#DD6B20") if score < 70
          else colors.HexColor("#D69E2E") if score < 90
          else colors.HexColor("#38A169"))
    res_s = ParagraphStyle("Res", parent=styles["Heading1"],
                            fontSize=20, textColor=rc, alignment=1, spaceAfter=4)
    story += [
        Paragraph("Resultado Final", h2_s),
        Paragraph(f"{data.get('classificacao', '')} — {score:.2f} / 100", res_s),
        Spacer(1, 12),
    ]

    # Parecer — converte markdown para flowables formatados
    story.append(Paragraph("Parecer Técnico (Gerado por IA)", h2_s))
    story.extend(
        _parecer_to_flowables(parecer, body_s, h2_s, h3_s, navy, teal, bgGray)
    )

    story += [
        Spacer(1, 16),
        Paragraph(
            "Documento gerado automaticamente — Sistema de Compliance e Risco — Garantidora Premiatto",
            small_s,
        ),
    ]

    doc.build(story)
    buffer.seek(0)
    return buffer


# ═════════════════════════════════════════════════════════════════════════════
# 7. AUTENTICAÇÃO
# ═════════════════════════════════════════════════════════════════════════════

_CREDENTIALS = {
    "compliance.joaogarcia@bancopremiatto.com.br":
        "6c95e1a64c36008239a4d9bedbb674b38bef6c59f7bcb922b6a51a0849a94df6",
}


def _hash(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def _login_page(logo_png: Optional[str]) -> None:
    st.markdown("""
    <style>
    .stApp { background: #f0f4f8 !important; }
    .login-card {
        background: #ffffff;
        border-radius: 16px;
        padding: 2.5rem 2.5rem 2rem;
        box-shadow: 0 4px 24px rgba(26,54,93,0.12);
        max-width: 420px;
        margin: 0 auto;
    }
    </style>
    """, unsafe_allow_html=True)

    _, col, _ = st.columns([1, 2, 1])
    with col:
        if logo_png:
            st.image(logo_png, use_container_width=True)
        else:
            st.markdown("<h2 style='text-align:center;color:#1a365d;'>Garantidora Premiatto</h2>", unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        with st.form("login_form"):
            email    = st.text_input("E-mail", placeholder="seu@email.com.br")
            password = st.text_input("Senha", type="password", placeholder="••••••••")
            entrar   = st.form_submit_button("Entrar", use_container_width=True)

        if entrar:
            email = email.strip().lower()
            if email in _CREDENTIALS and _CREDENTIALS[email] == _hash(password):
                st.session_state["authenticated"] = True
                st.session_state["user_email"]    = email
                st.rerun()
            else:
                st.error("E-mail ou senha incorretos.")


# ═════════════════════════════════════════════════════════════════════════════
# 8. INTERFACE STREAMLIT
# ═════════════════════════════════════════════════════════════════════════════

def _get_logo_png() -> Optional[str]:
    """Converte Logo_GPRemiatto.pdf → PNG na pasta assets e retorna o caminho."""
    assets_dir = Path(__file__).parent / "assets"
    png_path   = assets_dir / "logo_premiatto.png"
    pdf_path   = assets_dir / "Logo_GPRemiatto.pdf"

    if png_path.exists():
        return str(png_path)
    if pdf_path.exists():
        try:
            import fitz  # PyMuPDF
            doc  = fitz.open(str(pdf_path))
            page = doc[0]
            mat  = fitz.Matrix(3, 3)       # 3× zoom → alta resolução
            pix  = page.get_pixmap(matrix=mat, alpha=False)
            pix.save(str(png_path))
            doc.close()
            return str(png_path)
        except Exception:
            pass
    return None


def main() -> None:
    st.set_page_config(
        page_title="Compliance & Risco — Garantidora Premiatto",
        page_icon="🏦",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    logo_png = _get_logo_png()

    if not st.session_state.get("authenticated"):
        _login_page(logo_png)
        st.stop()

    st.markdown("""
    <style>
    /* ── Página principal ── */
    .stApp { background: #f0f4f8 !important; }
    .block-container { padding-top: 1.5rem !important; }

    /* ── Sidebar ── */
    [data-testid="stSidebar"] {
        background: #ffffff !important;
        border-right: 1px solid #dde3ed !important;
    }
    [data-testid="stSidebar"] label,
    [data-testid="stSidebar"] p,
    [data-testid="stSidebar"] span,
    [data-testid="stSidebar"] div { color: #2d3748 !important; }
    [data-testid="stSidebar"] h1,
    [data-testid="stSidebar"] h2,
    [data-testid="stSidebar"] h3 { color: #1a365d !important; font-weight: 700 !important; }
    [data-testid="stSidebar"] hr { border-color: #e2e8f0 !important; margin: 0.6rem 0 !important; }

    /* ── Botão processar ── */
    div[data-testid="stButton"] > button {
        background: linear-gradient(135deg, #1a365d, #2b6cb0) !important;
        color: #ffffff !important; border: none !important;
        font-weight: 700 !important; border-radius: 8px !important;
        width: 100% !important; padding: 0.65rem !important;
        font-size: 1rem !important; letter-spacing: 0.02em !important;
        box-shadow: 0 2px 8px rgba(26,54,93,0.25) !important;
        transition: opacity .2s !important;
    }
    div[data-testid="stButton"] > button p,
    div[data-testid="stButton"] > button span,
    div[data-testid="stButton"] > button div {
        color: #ffffff !important;
    }
    div[data-testid="stButton"] > button:hover { opacity: 0.88 !important; }

    /* ── Métricas ── */
    [data-testid="stMetric"] {
        background: #ffffff !important;
        border: 1px solid #dde3ed !important;
        border-radius: 10px !important;
        padding: 0.8rem 1rem !important;
        box-shadow: 0 1px 4px rgba(0,0,0,.06) !important;
    }
    [data-testid="stMetricLabel"] { color: #4a5568 !important; font-size: 0.8rem !important; }
    [data-testid="stMetricValue"] { color: #1a365d !important; font-weight: 800 !important; }
    [data-testid="stMetricDelta"] { color: #2b6cb0 !important; }

    /* ── Expanders ── */
    [data-testid="stExpander"] {
        background: #ffffff !important;
        border: 1px solid #dde3ed !important;
        border-radius: 10px !important;
        box-shadow: 0 1px 4px rgba(0,0,0,.05) !important;
    }
    [data-testid="stExpander"] summary { color: #1a365d !important; font-weight: 600 !important; }

    /* ── Dataframe / tabelas ── */
    [data-testid="stDataFrame"] { border-radius: 8px !important; overflow: hidden !important; }
    thead tr th { background: #1a365d !important; color: #ffffff !important; }
    tbody tr:nth-child(even) td { background: #f7fafc !important; }

    /* ── Info / Warning / Success ── */
    [data-testid="stAlert"] { border-radius: 8px !important; }

    /* ── Texto geral ── */
    .stMarkdown p, .stMarkdown li { color: #2d3748 !important; }
    h1, h2, h3 { color: #1a365d !important; }

    /* ── Cabeçalho principal — força branco ── */
    .header-principal h1,
    .header-principal p { color: #ffffff !important; }

    /* ── Score card ── */
    .score-card {
        border-radius: 14px; padding: 1.8rem 1rem;
        text-align: center; margin-bottom: 1rem;
        box-shadow: 0 2px 12px rgba(0,0,0,.08);
    }

    /* ── Barra de progresso ── */
    [data-testid="stProgress"] > div > div {
        background: linear-gradient(90deg,#2b6cb0,#4299e1) !important;
        border-radius: 4px !important;
    }

    /* ── File uploader ── */
    [data-testid="stFileUploader"] {
        background: #f7fafc !important;
        border: 1.5px dashed #a0aec0 !important;
        border-radius: 8px !important;
    }

    /* ── Number input ── */
    [data-testid="stNumberInput"] input {
        background: #f7fafc !important;
        border: 1px solid #cbd5e0 !important;
        color: #2d3748 !important;
        border-radius: 6px !important;
    }
    </style>
    """, unsafe_allow_html=True)

    # ── Cabeçalho principal ───────────────────────────────────────────────────
    st.markdown("""
    <div class="header-principal" style="background:linear-gradient(135deg,#1a365d,#2b6cb0);
                border-radius:14px; padding:1.4rem 2rem; margin-bottom:1.5rem;
                box-shadow:0 4px 16px rgba(26,54,93,0.18);">
        <h1 style="color:#ffffff !important;margin:0;font-size:1.75rem;font-weight:800;letter-spacing:-0.01em;">
            🏦 Sistema de Compliance e Risco
        </h1>
        <p style="color:#ffffff !important;margin:0.3rem 0 0;font-size:0.95rem;">
            Análise automatizada de garantias · Garantidora Premiatto
        </p>
    </div>
    """, unsafe_allow_html=True)

    # ── Barra lateral ─────────────────────────────────────────────────────────
    with st.sidebar:
        if logo_png:
            st.image(logo_png, use_container_width=True)
            st.markdown("---")
        user_email = st.session_state.get("user_email", "")
        st.markdown(f"<small style='color:#718096;'>Logado como<br><b>{user_email}</b></small>", unsafe_allow_html=True)
        if st.button("Sair", use_container_width=True):
            st.session_state.clear()
            st.rerun()
        st.markdown("---")
        st.markdown("### 📂 Relatórios Bureau")
        st.markdown("---")
        serasa_pf_file = st.file_uploader("Serasa PF — Pessoa Física",  type=["pdf"])
        serasa_pj_file = st.file_uploader("Serasa PJ — Pessoa Jurídica", type=["pdf"])
        neoway_file    = st.file_uploader("Due Diligence Neoway",         type=["pdf"])

        st.markdown("---")
        st.markdown("### 📊 Documentos Adicionais *(opcionais)*")
        dre_file = st.file_uploader(
            "Balanço Patrimonial / DRE",
            type=["pdf"],
            help="Bureau 30% | Financeiro 40% | Capacidade 30%",
        )
        if dre_file:
            st.success("DRE/Balanço carregado")

        bp_file = st.file_uploader(
            "Business Plan",
            type=["pdf"],
            help=(
                "Avaliado pela IA em 5 dimensões.\n"
                "Sem DRE: Bureau 35% | Cap 25% | BP 40%\n"
                "Com DRE: Bureau 20% | DRE 30% | Cap 20% | BP 30%"
            ),
        )
        if bp_file:
            st.success("Business Plan carregado")

        finalidade_file = st.file_uploader(
            "Finalidade do Recurso",
            type=["pdf"],
            help="Documento descrevendo a finalidade do uso da garantia. "
                 "Usado como contexto qualitativo pela IA no parecer.",
        )
        if finalidade_file:
            st.success("Finalidade do recurso carregada")

        st.markdown("---")
        st.markdown("### 💰 Parâmetros da Garantia")

        def _parse_br(s: str) -> float:
            """'6.800.000,00' → 6800000.0"""
            return float(s.replace(".", "").replace(",", "."))

        def _fmt_valor():
            val = st.session_state.get("_valor_str", "").strip()
            try:
                num = int(_parse_br(val))
            except (ValueError, AttributeError):
                # Fallback: só dígitos (usuário digitou sem formatação)
                digits = re.sub(r"[^\d]", "", val)
                num = int(digits) if digits else 0
            if num >= 0:
                st.session_state["_valor_str"] = (
                    f"{num:,}".replace(",", ".") + ",00"
                )

        if "_valor_str" not in st.session_state:
            st.session_state["_valor_str"] = "100.000,00"

        st.text_input(
            "Valor da Garantia (R$)",
            key="_valor_str",
            on_change=_fmt_valor,
            placeholder="Ex: 1.000.000,00",
        )

        try:
            valor_garantia = _parse_br(st.session_state["_valor_str"])
        except Exception:
            valor_garantia = 0.0
        tempo_vigencia = st.number_input(
            "Tempo de Vigência (meses)",
            min_value=1, max_value=360, value=12, step=1,
        )

        st.markdown("---")
        processar = st.button("🚀 Processar e Calcular Risco")

    # ── Estado inicial ─────────────────────────────────────────────────────────
    if not processar:
        c1, c2, c3 = st.columns(3)
        c1.info("**1.** Faça upload dos PDFs no painel lateral")
        c2.info("**2.** Informe valor da garantia e vigência")
        c3.info("**3.** Clique em **Processar e Calcular Risco**")

        with st.expander("ℹ️ Metodologia de Cálculo"):
            st.markdown("""
**Modo Padrão** (sem DRE):

| Componente | Fonte | Peso | Normalização |
|---|---|---|---|
| Saúde Financeira | Serasa PF/PJ | **40 %** | `Score ÷ 10` |
| Compliance | Neoway | **30 %** | `100 − Score` (inversão) |
| Capacidade de Pagamento | Neoway (Faturamento) | **30 %** | 100 se capaz; 0 se não |

---

**Modo Análise Financeira** (com DRE/Balanço):

| Componente | Fonte | Peso |
|---|---|---|
| Bureau (Serasa + Neoway) | PDFs Bureau | **30 %** |
| Índices Financeiros | DRE / Balanço | **40 %** |
| Capacidade de Pagamento | Neoway (Faturamento) | **30 %** |

*Índices financeiros calculados:*

| Índice | Peso no Bloco | Referência |
|---|---|---|
| Liquidez Corrente | 40 % | Ideal > 1,2 |
| Cobertura da Garantia (EBITDA ÷ Garantia) | 40 % | ≥ 1,0 = 100 pts |
| Margem Líquida | 20 % | ≥ 20% = 100 pts |

**Classificação de Risco:**

| Score | Nível |
|---|---|
| 90–100 | 🟢 Risco Mínimo |
| 70–89  | 🟡 Risco Moderado |
| 40–69  | 🟠 Risco Moderado-Alto |
| 0–39   | 🔴 Risco Alto |
""")
        return

    # ── Processamento ─────────────────────────────────────────────────────────
    prog = st.progress(0)
    msg  = st.empty()

    results: dict = {
        "valor_garantia":        valor_garantia,
        "tempo_vigencia":        tempo_vigencia,
        "score_serasa_pf":       None,
        "score_serasa_pj":       None,
        "score_neoway_original": None,
        "faturamento_min":       None,
        "faturamento_max":       None,
        "pontos_serasa":         0.0,
        "pontos_neoway":         0.0,
        "pontos_capacidade":     0,
        "modo_dre":              dre_file is not None,
        "modo_bp":               bp_file is not None,
        # campos DRE
        "ativo_circulante":      None,
        "passivo_circulante":    None,
        "estoque":               None,
        "patrimonio_liquido":    None,
        "receita_liquida":       None,
        "lucro_liquido":         None,
        "ebitda":                None,
    }
    indices:      Optional[dict] = None
    avaliacao_bp: Optional[dict] = None

    # 1 — Serasa PF
    msg.text("📄 Extraindo Serasa PF…")
    prog.progress(8)
    if serasa_pf_file:
        txt = extract_text_from_pdf(serasa_pf_file)
        results["score_serasa_pf"] = extract_serasa_score(txt)

    # 2 — Serasa PJ
    msg.text("📄 Extraindo Serasa PJ…")
    prog.progress(18)
    if serasa_pj_file:
        txt = extract_text_from_pdf(serasa_pj_file)
        results["score_serasa_pj"] = extract_serasa_score(txt)

    # 3 — Neoway
    msg.text("📄 Extraindo Neoway Due Diligence…")
    prog.progress(30)
    if neoway_file:
        txt = extract_text_from_pdf(neoway_file)
        results["score_neoway_original"]                         = extract_neoway_compliance_score(txt)
        results["faturamento_min"], results["faturamento_max"]   = extract_neoway_faturamento(txt)

    # 4 — DRE / Balanço (novo)
    if dre_file:
        msg.text("📊 Extraindo DRE / Balanço Patrimonial…")
        prog.progress(45)
        txt_dre = extract_text_from_pdf(dre_file)
        dados_dre = extract_dre_balanco(txt_dre)
        for k, v in dados_dre.items():
            results[k] = v

        msg.text("🧮 Calculando índices financeiros…")
        prog.progress(55)
        indices = calcular_indices_financeiros(dados_dre, valor_garantia)

        extraidos = [k for k, v in dados_dre.items() if v is not None]
        nao_extraidos = [k for k, v in dados_dre.items() if v is None]
        if extraidos:
            st.info(f"DRE/Balanço: extraídos {len(extraidos)}/7 campos — {', '.join(extraidos)}")
        if nao_extraidos:
            st.warning(
                f"Campos não extraídos do DRE: **{', '.join(nao_extraidos)}**. "
                "Verifique se o PDF contém texto pesquisável (não apenas imagem)."
            )

    # 5 — Business Plan (avaliação IA)
    if bp_file:
        msg.text("📋 Avaliando Business Plan com IA…")
        prog.progress(58)
        txt_bp = extract_text_from_pdf(bp_file)
        if txt_bp:
            avaliacao_bp = avaliar_business_plan(txt_bp, valor_garantia, tempo_vigencia)
            if avaliacao_bp:
                st.info(
                    f"Business Plan avaliado: **{avaliacao_bp.get('score_geral', 0):.1f}/100** "
                    f"— {avaliacao_bp.get('classificacao', '')}"
                )
            else:
                st.warning(
                    "⚠️ Não foi possível avaliar o Business Plan via IA. "
                    "Verifique a API Key e se o PDF contém texto pesquisável."
                )
        else:
            st.warning("⚠️ Business Plan: PDF sem texto extraível.")
        results["modo_bp"] = avaliacao_bp is not None

    # 6 — Finalidade do Recurso (contexto qualitativo para a IA)
    finalidade_text: Optional[str] = None
    if finalidade_file:
        msg.text("📝 Extraindo finalidade do recurso…")
        prog.progress(62)
        finalidade_text = extract_text_from_pdf(finalidade_file)
        if finalidade_text:
            st.info("Finalidade do recurso extraída e será usada como contexto no parecer.")
        else:
            st.warning("⚠️ Finalidade do Recurso: PDF sem texto extraível.")

    # 7 — Normalização Bureau
    msg.text("🧮 Normalizando scores de bureau…")
    prog.progress(65)

    serasa_vals = [v for v in [results["score_serasa_pf"], results["score_serasa_pj"]] if v is not None]
    if serasa_vals:
        results["pontos_serasa"] = round(max(serasa_vals) / 10, 2)
    else:
        st.warning("⚠️ Score Serasa não encontrado. Atribuindo **0 pts**.")

    if results["score_neoway_original"] is not None:
        results["pontos_neoway"] = round(100 - results["score_neoway_original"], 2)
    else:
        st.warning("⚠️ Score Neoway não encontrado. Atribuindo **0 pts**.")

    # 6 — Capacidade de Pagamento
    msg.text("💰 Calculando capacidade de pagamento…")
    prog.progress(72)
    results["pontos_capacidade"] = calculate_payment_capacity(
        results["faturamento_max"], valor_garantia, tempo_vigencia,
    )
    if results["faturamento_max"] is None:
        st.warning("⚠️ Faturamento não encontrado. Atribuindo **0 pts**.")

    # 7 — Score Final
    msg.text("📈 Calculando score final…")
    prog.progress(84)

    # Calcula pontos_bureau (combinação Serasa + Neoway) para todos os modos que o usam
    ps, pn = results["pontos_serasa"], results["pontos_neoway"]
    if ps > 0 and pn > 0:
        pontos_bureau_combinado = round((ps + pn) / 2, 2)
    elif ps > 0:
        pontos_bureau_combinado = ps
    elif pn > 0:
        pontos_bureau_combinado = pn
    else:
        pontos_bureau_combinado = 0.0

    tem_dre = results["modo_dre"] and indices and indices.get("pontos_financeiro") is not None
    tem_bp  = avaliacao_bp is not None
    pontos_bp = float(avaliacao_bp["score_geral"]) if tem_bp else 0.0

    if tem_dre and tem_bp:
        # Modo DRE + BP: Bureau 20% | Financeiro 30% | Cap 20% | BP 30%
        pf = indices["pontos_financeiro"]
        results["pontos_bureau"]     = pontos_bureau_combinado
        results["pontos_financeiro"] = pf
        results["pontos_bp"]         = pontos_bp
        results["componente_bureau"]     = round(pontos_bureau_combinado * 0.20, 2)
        results["componente_financeiro"] = round(pf                      * 0.30, 2)
        results["componente_capacidade"] = round(results["pontos_capacidade"] * 0.20, 2)
        results["componente_bp"]         = round(pontos_bp              * 0.30, 2)
        results["score_final"] = calculate_final_score_com_dre_bp(
            pontos_bureau_combinado, pf, results["pontos_capacidade"], pontos_bp,
        )

    elif tem_bp:
        # Modo BP (sem DRE): Bureau 35% | Cap 25% | BP 40%
        results["pontos_bureau"]     = pontos_bureau_combinado
        results["pontos_bp"]         = pontos_bp
        results["componente_bureau"]     = round(pontos_bureau_combinado       * 0.35, 2)
        results["componente_capacidade"] = round(results["pontos_capacidade"] * 0.25, 2)
        results["componente_bp"]         = round(pontos_bp                    * 0.40, 2)
        results["score_final"] = calculate_final_score_com_bp(
            pontos_bureau_combinado, results["pontos_capacidade"], pontos_bp,
        )
        results["modo_dre"] = False

    elif tem_dre:
        # Modo DRE: Bureau 30% | Financeiro 40% | Cap 30%
        pf = indices["pontos_financeiro"]
        results["pontos_bureau"]     = pontos_bureau_combinado
        results["pontos_financeiro"] = pf
        results["componente_bureau"]     = round(pontos_bureau_combinado * 0.30, 2)
        results["componente_financeiro"] = round(pf                      * 0.40, 2)
        results["componente_capacidade"] = round(results["pontos_capacidade"] * 0.30, 2)
        results["score_final"] = calculate_final_score_com_dre(
            pontos_bureau_combinado, pf, results["pontos_capacidade"],
        )

    else:
        # Modo padrão: Serasa 40% | Neoway 30% | Cap 30%
        results["componente_saude"]      = round(results["pontos_serasa"]     * 0.40, 2)
        results["componente_compliance"] = round(results["pontos_neoway"]     * 0.30, 2)
        results["componente_capacidade"] = round(results["pontos_capacidade"] * 0.30, 2)
        results["score_final"] = calculate_final_score_standard(
            results["pontos_serasa"], results["pontos_neoway"], results["pontos_capacidade"],
        )
        if results["modo_dre"]:
            st.warning("DRE carregado mas sem índices calculáveis. Usando metodologia padrão.")
            results["modo_dre"] = False

    label, cor, _ = get_risk_classification(results["score_final"])
    emoji_map = {"Risco Alto": "🔴", "Risco Moderado-Alto": "🟠",
                 "Risco Moderado": "🟡", "Risco Mínimo": "🟢"}
    results["classificacao"] = f"{emoji_map.get(label, '')} {label}"
    results["cor"]           = cor

    # 8 — Parecer IA
    msg.text("🤖 Gerando parecer técnico com IA…")
    prog.progress(93)
    parecer = generate_technical_opinion(results, indices, avaliacao_bp, finalidade_text)

    prog.progress(100)
    msg.text("✅ Análise concluída!")
    time.sleep(0.8)
    prog.empty()
    msg.empty()

    # ── Exibição dos Resultados ───────────────────────────────────────────────
    st.markdown("## 📊 Resultado da Análise")

    modo_dre = results["modo_dre"]
    modo_bp  = tem_bp
    if tem_dre and tem_bp:
        st.info(
            "**Metodologia Completa ativa** — DRE + Business Plan: "
            "Bureau 20% | Índices Financeiros 30% | Capacidade 20% | Business Plan 30%"
        )
    elif tem_bp:
        st.info(
            "**Metodologia com Business Plan ativa**: "
            "Bureau 35% | Capacidade 25% | Business Plan 40%"
        )
    elif modo_dre:
        st.info(
            "**Metodologia Avançada ativa** — DRE/Balanço incluído: "
            "Bureau 30% | Índices Financeiros 40% | Capacidade 30%"
        )
    st.markdown("---")

    score = results["score_final"]
    cor   = results["cor"]
    st.markdown(f"""
    <div class="score-card"
         style="background:linear-gradient(135deg,{cor}18,{cor}30); border:2px solid {cor};">
        <div style="font-size:0.75rem;color:#718096;font-weight:700;
                    text-transform:uppercase;letter-spacing:0.05em;">Score Final de Risco</div>
        <div style="font-size:4rem;font-weight:900;color:{cor};line-height:1.1;">
            {score:.2f}
            <span style="font-size:1.2rem;font-weight:400;color:#718096;">/ 100</span>
        </div>
        <div style="font-size:1.5rem;margin-top:0.4rem;">{results['classificacao']}</div>
    </div>
    """, unsafe_allow_html=True)
    st.markdown("---")

    # ── Colunas de detalhe ────────────────────────────────────────────────────
    # Determine number of detail columns based on active mode
    _ncols = 4 if (tem_dre and tem_bp) else 3
    _detail_cols = st.columns(_ncols)

    # Bureau column (always present)
    with _detail_cols[0]:
        if modo_dre or tem_bp:
            _bur_peso = "20%" if (tem_dre and tem_bp) else ("35%" if tem_bp else "30%")
            st.markdown("#### 📋 Bureau (Serasa + Neoway)")
            st.metric(
                f"Pontos (peso {_bur_peso})",
                f"{results.get('pontos_bureau', 0):.1f} / 100",
                delta=f"Contribuição: {results.get('componente_bureau', 0):.2f} pts",
            )
            pf = results["score_serasa_pf"]
            pj = results["score_serasa_pj"]
            orig = results["score_neoway_original"]
            st.markdown(f"- Serasa PF: **{pf if pf is not None else 'N/D'}** / 1000")
            st.markdown(f"- Serasa PJ: **{pj if pj is not None else 'N/D'}** / 1000")
            st.markdown(f"- Neoway (orig): **{orig if orig is not None else 'N/D'}** / 100")
        else:
            # Standard mode — show individual Serasa and Neoway
            st.markdown("#### 💰 Saúde Financeira")
            st.metric(
                "Pontos (peso 40%)",
                f"{results['pontos_serasa']:.1f} / 100",
                delta=f"Contribuição: {results['componente_saude']:.2f} pts",
            )
            pf = results["score_serasa_pf"]
            pj = results["score_serasa_pj"]
            st.markdown(f"- Serasa PF: **{pf if pf is not None else 'N/D'}** / 1000")
            st.markdown(f"- Serasa PJ: **{pj if pj is not None else 'N/D'}** / 1000")

    # Second column: DRE (if active) or Compliance (standard mode)
    with _detail_cols[1]:
        if tem_dre:
            _fin_peso = "30%" if tem_bp else "40%"
            st.markdown("#### 📊 Índices Financeiros (DRE)")
            pf_pts = results.get("pontos_financeiro", 0) or 0
            st.metric(
                f"Pontos (peso {_fin_peso})",
                f"{pf_pts:.1f} / 100",
                delta=f"Contribuição: {results.get('componente_financeiro', 0):.2f} pts",
            )
            if indices:
                lc = indices.get("liquidez_corrente")
                cg = indices.get("cobertura_garantia")
                ml = indices.get("margem_liquida")
                lc_str  = f"{lc:.2f}x"    if lc is not None else "N/D"
                cg_str  = f"{cg:.2f}x"    if cg is not None else "N/D"
                ml_str  = f"{ml*100:.1f}%" if ml is not None else "N/D"
                ptl_str = f"{indices.get('pts_liquidez',  0):.0f}" if indices.get("pts_liquidez")  is not None else "N/D"
                ptc_str = f"{indices.get('pts_cobertura', 0):.0f}" if indices.get("pts_cobertura") is not None else "N/D"
                ptm_str = f"{indices.get('pts_margem',    0):.0f}" if indices.get("pts_margem")    is not None else "N/D"
                st.markdown(f"- Liquidez Corrente: **{lc_str}** → {ptl_str} pts")
                st.markdown(f"- Cob. Garantia: **{cg_str}** → {ptc_str} pts")
                st.markdown(f"- Margem Líquida: **{ml_str}** → {ptm_str} pts")
        elif tem_bp:
            # BP mode without DRE — second col is Capacidade
            _cap_peso = "25%"
            st.markdown("#### 📈 Capacidade de Pagamento")
            st.metric(
                f"Pontos (peso {_cap_peso})",
                f"{results['pontos_capacidade']} / 100",
                delta=f"Contribuição: {results['componente_capacidade']:.2f} pts",
            )
            fat_max = results["faturamento_max"]
            if fat_max:
                fat_m = fat_max / 12
                cap   = fat_m * tempo_vigencia
                st.markdown(f"- Faturamento Mensal: **R$ {fat_m:,.2f}**")
                st.markdown(f"- Capacidade: **R$ {cap:,.2f}**")
                ok = "✅ Suficiente" if cap >= valor_garantia else "❌ Insuficiente"
                st.markdown(f"- Situação: **{ok}**")
            else:
                st.error("Faturamento não extraído")
        else:
            # Standard mode — Compliance
            st.markdown("#### 🛡️ Compliance")
            st.metric(
                "Pontos (peso 30%)",
                f"{results['pontos_neoway']:.1f} / 100",
                delta=f"Contribuição: {results['componente_compliance']:.2f} pts",
            )
            orig = results["score_neoway_original"]
            st.markdown(f"- Score Neoway orig.: **{orig if orig is not None else 'N/D'}**")
            st.markdown(f"- Normalizado (inv.): **{results['pontos_neoway']:.1f}**")

    # Third column: BP (if BP without DRE → this slot is BP) OR Capacidade (DRE-only / standard)
    with _detail_cols[2]:
        if tem_dre and tem_bp:
            # 4-col layout: [Bureau | DRE | Capacidade | BP] — this slot is Capacidade
            st.markdown("#### 📈 Capacidade de Pagamento")
            st.metric(
                "Pontos (peso 20%)",
                f"{results['pontos_capacidade']} / 100",
                delta=f"Contribuição: {results['componente_capacidade']:.2f} pts",
            )
            fat_max = results["faturamento_max"]
            if fat_max:
                fat_m = fat_max / 12
                cap   = fat_m * tempo_vigencia
                st.markdown(f"- Faturamento Mensal: **R$ {fat_m:,.2f}**")
                st.markdown(f"- Capacidade: **R$ {cap:,.2f}**")
                ok = "✅ Suficiente" if cap >= valor_garantia else "❌ Insuficiente"
                st.markdown(f"- Situação: **{ok}**")
            else:
                st.error("Faturamento não extraído")
        elif tem_bp:
            # BP-only 3-col layout: [Bureau | Cap | BP] — this slot is BP
            st.markdown("#### 📑 Business Plan")
            bp_geral = avaliacao_bp.get("score_geral", 0) if avaliacao_bp else 0
            st.metric(
                "Pontos (peso 40%)",
                f"{bp_geral:.1f} / 100",
                delta=f"Contribuição: {results.get('componente_bp', 0):.2f} pts",
            )
            if avaliacao_bp:
                st.markdown(f"- Classificação: **{avaliacao_bp.get('classificacao', 'N/D')}**")
                for dim_key, dim_label in BP_DIMENSOES.items():
                    d = avaliacao_bp.get(dim_key, {})
                    sc = d.get("score", "N/D")
                    st.markdown(f"- {dim_label}: **{sc}/100**")
        else:
            # DRE-only or standard 3-col: [Bureau/Saúde | DRE/Compliance | Capacidade]
            _cap_peso = "30%"
            st.markdown("#### 📈 Capacidade de Pagamento")
            st.metric(
                f"Pontos (peso {_cap_peso})",
                f"{results['pontos_capacidade']} / 100",
                delta=f"Contribuição: {results['componente_capacidade']:.2f} pts",
            )
            fat_max = results["faturamento_max"]
            if fat_max:
                fat_m = fat_max / 12
                cap   = fat_m * tempo_vigencia
                st.markdown(f"- Faturamento Mensal: **R$ {fat_m:,.2f}**")
                st.markdown(f"- Capacidade: **R$ {cap:,.2f}**")
                ok = "✅ Suficiente" if cap >= valor_garantia else "❌ Insuficiente"
                st.markdown(f"- Situação: **{ok}**")
            else:
                st.error("Faturamento não extraído")

    # Fourth column: BP (only in DRE + BP mode)
    if tem_dre and tem_bp:
        with _detail_cols[3]:
            st.markdown("#### 📑 Business Plan")
            bp_geral = avaliacao_bp.get("score_geral", 0) if avaliacao_bp else 0
            st.metric(
                "Pontos (peso 30%)",
                f"{bp_geral:.1f} / 100",
                delta=f"Contribuição: {results.get('componente_bp', 0):.2f} pts",
            )
            if avaliacao_bp:
                st.markdown(f"- Classificação: **{avaliacao_bp.get('classificacao', 'N/D')}**")
                for dim_key, dim_label in BP_DIMENSOES.items():
                    d = avaliacao_bp.get(dim_key, {})
                    sc = d.get("score", "N/D")
                    st.markdown(f"- {dim_label}: **{sc}/100**")

    # ── Detalhamento DRE (tabela expandível) ─────────────────────────────────
    if modo_dre and any(results.get(k) for k in [
        "ativo_circulante", "passivo_circulante", "receita_liquida",
        "lucro_liquido", "ebitda"
    ]):
        st.markdown("---")
        with st.expander("📑 Detalhamento — Contas DRE / Balanço Extraídas", expanded=True):
            def fv(v): return f"R$ {v:,.2f}" if v is not None else "—"
            d1, d2 = st.columns(2)
            with d1:
                st.markdown("**Balanço Patrimonial**")
                st.markdown(f"- Ativo Circulante: **{fv(results.get('ativo_circulante'))}**")
                st.markdown(f"- Passivo Circulante: **{fv(results.get('passivo_circulante'))}**")
                st.markdown(f"- Estoque: **{fv(results.get('estoque'))}**")
                st.markdown(f"- Patrimônio Líquido: **{fv(results.get('patrimonio_liquido'))}**")
            with d2:
                st.markdown("**DRE**")
                st.markdown(f"- Receita Líquida: **{fv(results.get('receita_liquida'))}**")
                st.markdown(f"- Lucro Líquido: **{fv(results.get('lucro_liquido'))}**")
                st.markdown(f"- EBITDA / LAJIDA: **{fv(results.get('ebitda'))}**")

    # ── Business Plan — Detalhamento ─────────────────────────────────────────
    if tem_bp and avaliacao_bp:
        st.markdown("---")
        with st.expander("📑 Detalhamento — Avaliação do Business Plan", expanded=True):
            bp_cols = st.columns([2, 1])
            with bp_cols[0]:
                st.markdown("**Pontuação por Dimensão**")
                for dim_key, dim_label in BP_DIMENSOES.items():
                    d = avaliacao_bp.get(dim_key, {})
                    sc  = d.get("score", 0)
                    obs = d.get("observacao", "")
                    st.markdown(f"- **{dim_label}**: {sc}/100")
                    if obs:
                        st.caption(obs)
            with bp_cols[1]:
                bp_geral = avaliacao_bp.get("score_geral", 0)
                bp_class = avaliacao_bp.get("classificacao", "")
                st.metric("Score Geral BP", f"{bp_geral:.0f} / 100")
                st.markdown(f"**Classificação:** {bp_class}")

            pts_fortes = avaliacao_bp.get("pontos_fortes", [])
            pts_atencao = avaliacao_bp.get("pontos_atencao", [])
            if pts_fortes or pts_atencao:
                bpc1, bpc2 = st.columns(2)
                with bpc1:
                    if pts_fortes:
                        st.markdown("**Pontos Fortes**")
                        for p in pts_fortes:
                            st.markdown(f"- ✅ {p}")
                with bpc2:
                    if pts_atencao:
                        st.markdown("**Pontos de Atenção**")
                        for p in pts_atencao:
                            st.markdown(f"- ⚠️ {p}")

    # ── Composição do Score ───────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### 🧮 Composição Detalhada do Score")
    try:
        import pandas as pd
        if tem_dre and tem_bp:
            pf_pts_val = indices.get("pontos_financeiro", 0) if indices else 0
            bp_geral_val = avaliacao_bp.get("score_geral", 0) if avaliacao_bp else 0
            df = pd.DataFrame({
                "Componente":   ["Bureau (Serasa+Neoway)", "Índices Financeiros (DRE)", "Cap. de Pagamento", "Business Plan"],
                "Pts Brutos":   [results.get("pontos_bureau", 0), pf_pts_val or 0,
                                 results["pontos_capacidade"], bp_geral_val],
                "Peso":         ["20%", "30%", "20%", "30%"],
                "Contribuição": [results.get("componente_bureau", 0),
                                 results.get("componente_financeiro", 0),
                                 results["componente_capacidade"],
                                 results.get("componente_bp", 0)],
            })
        elif tem_bp:
            bp_geral_val = avaliacao_bp.get("score_geral", 0) if avaliacao_bp else 0
            df = pd.DataFrame({
                "Componente":   ["Bureau (Serasa+Neoway)", "Cap. de Pagamento", "Business Plan"],
                "Pts Brutos":   [results.get("pontos_bureau", 0),
                                 results["pontos_capacidade"], bp_geral_val],
                "Peso":         ["35%", "25%", "40%"],
                "Contribuição": [results.get("componente_bureau", 0),
                                 results["componente_capacidade"],
                                 results.get("componente_bp", 0)],
            })
        elif modo_dre:
            pf_pts_val = indices.get("pontos_financeiro", 0) if indices else 0
            df = pd.DataFrame({
                "Componente":   ["Bureau (Serasa+Neoway)", "Índices Financeiros (DRE)", "Cap. de Pagamento"],
                "Pts Brutos":   [results.get("pontos_bureau", 0), pf_pts_val or 0, results["pontos_capacidade"]],
                "Peso":         ["30%", "40%", "30%"],
                "Contribuição": [results.get("componente_bureau", 0),
                                 results.get("componente_financeiro", 0),
                                 results["componente_capacidade"]],
            })
        else:
            df = pd.DataFrame({
                "Componente":   ["Saúde Financeira (Serasa)", "Compliance (Neoway)", "Cap. de Pagamento"],
                "Pts Brutos":   [results["pontos_serasa"], results["pontos_neoway"], results["pontos_capacidade"]],
                "Peso":         ["40%", "30%", "30%"],
                "Contribuição": [results["componente_saude"], results["componente_compliance"],
                                 results["componente_capacidade"]],
            })
        st.dataframe(df, use_container_width=True, hide_index=True)
    except ImportError:
        st.write("Instale pandas para visualização em tabela: `pip install pandas`")

    # ── Parecer Técnico ───────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### 🤖 Parecer Técnico (Gerado por IA)")
    with st.expander("Ver parecer completo", expanded=True):
        st.markdown(parecer)

    # ── Downloads ─────────────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### 📥 Download do Relatório")
    dl1, dl2 = st.columns(2)

    with dl1:
        txt_report = generate_txt_report(results, parecer, indices, avaliacao_bp, finalidade_text)
        st.download_button(
            "📄 Baixar Relatório (.txt)",
            data=txt_report,
            file_name=f"risco_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
            mime="text/plain",
            use_container_width=True,
        )
    with dl2:
        if REPORTLAB_OK:
            pdf_buf = generate_pdf_report(results, parecer, indices, avaliacao_bp, finalidade_text)
            if pdf_buf:
                st.download_button(
                    "📑 Baixar Relatório (.pdf)",
                    data=pdf_buf,
                    file_name=f"risco_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                )
        else:
            st.info("Para PDF: `pip install reportlab`")

    # ── Debug ─────────────────────────────────────────────────────────────────
    with st.expander("🔍 Debug — dados brutos"):
        col_r, col_i, col_bp = st.columns(3)
        with col_r:
            st.markdown("**Results**")
            st.json({k: v for k, v in results.items() if k != "cor"})
        with col_i:
            st.markdown("**Índices DRE**")
            st.json(indices or {})
        with col_bp:
            st.markdown("**Business Plan**")
            st.json(avaliacao_bp or {})


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    main()
