"""
core/scoring.py
Motor de score: normalizacao, indices financeiros e calculo do score final.
"""

from typing import Dict, Optional, Tuple


def calculate_payment_capacity(
    faturamento_max: Optional[float],
    valor_garantia: float,
    tempo_vigencia: int,
) -> int:
    """100 se Faturamento_Mensal x Vigencia >= Garantia, senao 0."""
    if not faturamento_max or faturamento_max <= 0:
        return 0
    return 100 if (faturamento_max / 12) * tempo_vigencia >= valor_garantia else 0


def calcular_indices_financeiros(
    dados: Dict[str, Optional[float]],
    valor_garantia: float,
) -> Dict[str, Optional[float]]:
    """
    Calcula os indices financeiros e converte cada um numa escala 0-100.

    Sub-indices:
      liquidez_corrente  (peso 40%) -- Ideal > 1.2
      cobertura_garantia (peso 40%) -- EBITDA / Garantia
      margem_liquida     (peso 20%) -- Lucro Liquido / Receita Liquida

    Retorna dict com valores brutos e pontuacoes normalizadas.
    """
    resumo: Dict[str, Optional[float]] = {}

    # Liquidez Corrente
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

    # Cobertura da Garantia (EBITDA / Garantia)
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

    # Margem Liquida
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

    # Score Financeiro Composto — peso: liquidez 40%, cobertura 40%, margem 20%
    pts_l = resumo.get("pts_liquidez")
    pts_c = resumo.get("pts_cobertura")
    pts_m = resumo.get("pts_margem")

    componentes = [(pts_l, 0.40), (pts_c, 0.40), (pts_m, 0.20)]
    peso_total = sum(w for v, w in componentes if v is not None)

    if peso_total > 0:
        score_fin = sum(v * w for v, w in componentes if v is not None)
        resumo["pontos_financeiro"] = round(score_fin / peso_total, 2)
    else:
        resumo["pontos_financeiro"] = None

    return resumo


def calculate_final_score_standard(
    pontos_serasa: float,
    pontos_neoway: float,
    pontos_capacidade: float,
) -> float:
    """Modo padrao (sem DRE): Serasa 40% | Neoway 30% | Capacidade 30%"""
    return round(
        pontos_serasa       * 0.40
        + pontos_neoway     * 0.30
        + pontos_capacidade * 0.30,
        2,
    )


def calculate_final_score_com_dre(
    pontos_bureau: float,
    pontos_financeiro: float,
    pontos_capacidade: float,
) -> float:
    """Modo DRE: Bureau 30% | Indices Financeiros 40% | Capacidade 30%"""
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
        return "Risco Minimo",        "#38A169", "🟢"
