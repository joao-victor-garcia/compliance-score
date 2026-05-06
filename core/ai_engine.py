"""
core/ai_engine.py
Motor de IA: avaliacao do Business Plan e geracao do parecer tecnico via Anthropic API.
"""

import json
import os
import re
from typing import Optional

import streamlit as st

try:
    from anthropic import Anthropic
    ANTHROPIC_OK = True
except ImportError:
    ANTHROPIC_OK = False

# Carrega a chave Anthropic do st.secrets (local: .streamlit/secrets.toml;
# Cloud: painel Secrets do Streamlit). Fallback para variavel de ambiente.
try:
    _secret_key = st.secrets.get("ANTHROPIC_API_KEY", "")
    if _secret_key:
        os.environ["ANTHROPIC_API_KEY"] = _secret_key
except Exception:
    pass

# Dimensoes avaliadas no Business Plan e seus rotulos de exibicao
BP_DIMENSOES = {
    "mercado":               "Analise de Mercado",
    "modelo_negocio":        "Modelo de Negocio",
    "projecoes_financeiras": "Projecoes Financeiras",
    "equipe_gestao":         "Equipe & Gestao",
    "estrategia_risco":      "Estrategia de Risco",
}


def avaliar_business_plan(
    text: str,
    valor_garantia: float,
    tempo_vigencia: int,
) -> Optional[dict]:
    """
    Envia o texto do Business Plan para a API Anthropic e retorna uma avaliacao
    estruturada em JSON com score por dimensao (0-100) e score geral.

    Retorna None se a API nao estiver configurada ou o texto for invalido.
    """
    if not ANTHROPIC_OK or not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    if not text or len(text.strip()) < 100:
        return None

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
        client = Anthropic()
        resp = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        m = re.search(r"\{[\s\S]+\}", raw)
        if m:
            data = json.loads(m.group())
            if "score_geral" in data:
                data["score_geral"] = float(data["score_geral"])
                return data
    except Exception as exc:
        st.warning(f"Avaliacao do Business Plan: {exc}")
    return None


def generate_technical_opinion(
    data: dict,
    indices: Optional[dict] = None,
    avaliacao_bp: Optional[dict] = None,
    finalidade_text: Optional[str] = None,
    documento_modal_text: Optional[str] = None,
) -> str:
    """
    Gera o parecer tecnico via Anthropic API com base nos dados da analise.
    Retorna string com o parecer formatado em markdown.
    """
    if not ANTHROPIC_OK:
        return "Biblioteca `anthropic` nao instalada. Execute: `pip install anthropic`"
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return "Chave ANTHROPIC_API_KEY nao configurada."

    fat_min  = data.get("faturamento_min")
    fat_max  = data.get("faturamento_max")
    fat_str  = f"R$ {fat_min:,.2f} a R$ {fat_max:,.2f}" if fat_min and fat_max else "Nao disponivel"
    fat_m    = (fat_max or 0) / 12
    cap      = fat_m * data.get("tempo_vigencia", 0)
    modo_dre = data.get("modo_dre", False)
    modo_bp  = avaliacao_bp is not None

    # Rotulo de risco Neoway
    _neo_orig = data.get("score_neoway_original")
    if _neo_orig is not None:
        if _neo_orig <= 30:
            _neo_label = "RISCO BAIXO"
        elif _neo_orig <= 50:
            _neo_label = "RISCO MODERADO"
        elif _neo_orig <= 70:
            _neo_label = "RISCO ALTO"
        else:
            _neo_label = "RISCO MUITO ALTO"
        _neo_str = f"{_neo_orig} / 100 -> {_neo_label}"
    else:
        _neo_str = "N/D"

    # Bloco bureau
    if modo_dre:
        bureau_bloco = (
            "BUREAU - SERASA + NEOWAY (Peso 30%)\n"
            f"  Score Serasa PF         : {data.get('score_serasa_pf', 'N/D')} / 1000  [escala: 0=alto risco -> 1000=risco minimo]\n"
            f"  Score Serasa PJ         : {data.get('score_serasa_pj', 'N/D')} / 1000  [escala: 0=alto risco -> 1000=risco minimo]\n"
            f"  Score Compliance Neoway : {_neo_str}  [escala: 0=risco minimo -> 100=risco maximo]\n"
            f"  Pontos Bureau (combinado): {data.get('pontos_bureau', 0):.1f} / 100\n"
            f"  Contribuicao ao score   : {data.get('componente_bureau', 0):.2f} pts"
        )
    else:
        bureau_bloco = (
            "SAUDE FINANCEIRA (Peso 40%)\n"
            f"  Score Serasa PF         : {data.get('score_serasa_pf', 'N/D')} / 1000  [escala: 0=alto risco -> 1000=risco minimo]\n"
            f"  Score Serasa PJ         : {data.get('score_serasa_pj', 'N/D')} / 1000  [escala: 0=alto risco -> 1000=risco minimo]\n"
            f"  Pontos normalizados     : {data.get('pontos_serasa', 0):.1f} / 100\n"
            f"  Contribuicao ao score   : {data.get('componente_saude', 0):.2f} pts\n\n"
            "COMPLIANCE (Peso 30%)\n"
            f"  Score Compliance Neoway : {_neo_str}  [escala: 0=risco minimo -> 100=risco maximo]\n"
            f"  Pontos normalizados (inv.): {data.get('pontos_neoway', 0):.1f} / 100  [invertido: 100-score -> maior pts = melhor]\n"
            f"  Contribuicao ao score   : {data.get('componente_compliance', 0):.2f} pts"
        )

    # Bloco DRE
    if modo_dre and indices:
        def fmt(v): return f"{v:,.2f}" if v is not None else "N/D"
        def fmtp(v): return f"{v:.1f}" if v is not None else "N/D"
        lc_raw = indices.get("liquidez_corrente")
        cg_raw = indices.get("cobertura_garantia")
        ml_raw = indices.get("margem_liquida")
        dre_bloco = (
            "\nANALISE FINANCEIRA - DRE / BALANCO (Peso 40%)\n"
            f"  Ativo Circulante    : R$ {fmt(data.get('ativo_circulante'))}\n"
            f"  Passivo Circulante  : R$ {fmt(data.get('passivo_circulante'))}\n"
            f"  Estoque             : R$ {fmt(data.get('estoque'))}\n"
            f"  Patrimonio Liquido  : R$ {fmt(data.get('patrimonio_liquido'))}\n"
            f"  Receita Liquida     : R$ {fmt(data.get('receita_liquida'))}\n"
            f"  Lucro Liquido       : R$ {fmt(data.get('lucro_liquido'))}\n"
            f"  EBITDA              : R$ {fmt(data.get('ebitda'))}\n"
            f"  Liquidez Corrente   : {fmt(lc_raw)}x  -> {fmtp(indices.get('pts_liquidez'))} pts  (ideal > 1,2)\n"
            f"  Cob. Garantia (EBITDA): {fmt(cg_raw)}x  -> {fmtp(indices.get('pts_cobertura'))} pts\n"
            f"  Margem Liquida      : {f'{lc_raw*100:.1f}%' if ml_raw is None else f'{ml_raw*100:.1f}%'}  -> {fmtp(indices.get('pts_margem'))} pts\n"
            f"  Pontos Financeiros  : {fmtp(indices.get('pontos_financeiro'))} / 100\n"
            f"  Contribuicao        : {data.get('componente_financeiro', 0):.2f} pts"
        )
    else:
        dre_bloco = ""

    # Bloco Finalidade
    if finalidade_text:
        _trunc = finalidade_text[:3000] + ("...[truncado]" if len(finalidade_text) > 3000 else "")
        _finalidade_bloco = "\n---\nFINALIDADE DO USO DO RECURSO (contexto qualitativo)\n---\n" + _trunc + "\n---\n"
    else:
        _finalidade_bloco = ""

    # Bloco Documento Modal (Processo ou Contrato)
    _modalidade = data.get("modalidade", "Financeira")
    if documento_modal_text and _modalidade in ("Processual", "Contratual"):
        _trunc_doc = documento_modal_text[:4000] + ("...[truncado]" if len(documento_modal_text) > 4000 else "")
        _label_doc = "PROCESSO JUDICIAL/ADMINISTRATIVO" if _modalidade == "Processual" else "CONTRATO"
        _documento_bloco = (
            f"\n---\nDOCUMENTO DA MODALIDADE — {_label_doc}\n"
            f"(Analise este documento para embasar o parecer sobre a garantia {_modalidade.lower()})\n---\n"
            + _trunc_doc + "\n---\n"
        )
    else:
        _documento_bloco = ""

    # Bloco Business Plan
    if modo_bp and avaliacao_bp:
        bp_peso = "30%" if modo_dre else "40%"
        bp_bloco = (
            f"\nBUSINESS PLAN (Peso {bp_peso})\n"
            f"  Score Geral           : {avaliacao_bp.get('score_geral', 0):.1f} / 100\n"
            f"  Classificacao BP      : {avaliacao_bp.get('classificacao', 'N/D')}\n"
            f"  Analise de Mercado    : {avaliacao_bp.get('mercado', {}).get('score', 'N/D')} pts"
            f" - {avaliacao_bp.get('mercado', {}).get('observacao', '')}\n"
            f"  Modelo de Negocio     : {avaliacao_bp.get('modelo_negocio', {}).get('score', 'N/D')} pts"
            f" - {avaliacao_bp.get('modelo_negocio', {}).get('observacao', '')}\n"
            f"  Projecoes Financeiras : {avaliacao_bp.get('projecoes_financeiras', {}).get('score', 'N/D')} pts"
            f" - {avaliacao_bp.get('projecoes_financeiras', {}).get('observacao', '')}\n"
            f"  Equipe & Gestao       : {avaliacao_bp.get('equipe_gestao', {}).get('score', 'N/D')} pts"
            f" - {avaliacao_bp.get('equipe_gestao', {}).get('observacao', '')}\n"
            f"  Estrategia de Risco   : {avaliacao_bp.get('estrategia_risco', {}).get('score', 'N/D')} pts"
            f" - {avaliacao_bp.get('estrategia_risco', {}).get('observacao', '')}\n"
            f"  Pontos Fortes         : {'; '.join(avaliacao_bp.get('pontos_fortes', []))}\n"
            f"  Pontos de Atencao     : {'; '.join(avaliacao_bp.get('pontos_atencao', []))}\n"
            f"  Contribuicao ao score : {data.get('componente_bp', 0):.2f} pts"
        )
    else:
        bp_bloco = ""

    _metodo = (
        "DRE + Business Plan" if (modo_dre and modo_bp) else
        "Business Plan"       if modo_bp else
        "Com DRE/Balanco"     if modo_dre else
        "Padrao"
    )

    prompt = f"""Você é um analista de risco sênior de uma garantidora brasileira.
Com base exclusivamente nos dados abaixo, redija um PARECER TÉCNICO formal em português.

DADOS DA ANALISE
Tipo de Pessoa               : {data.get('tipo_pessoa', 'N/D')}
Modalidade da Garantia       : {_modalidade}
Valor da Garantia Solicitada : R$ {data.get('valor_garantia', 0):,.2f}
Tempo de Vigencia            : {data.get('tempo_vigencia', 0)} meses
Metodologia                  : {_metodo}

{bureau_bloco}{dre_bloco}{bp_bloco}

CAPACIDADE DE PAGAMENTO (Peso 30%)
  Faturamento Estimado  : {fat_str}
  Faturamento Mensal    : R$ {fat_m:,.2f}
  Capacidade no Periodo : R$ {cap:,.2f}
  Pontos                : {data.get('pontos_capacidade', 0)} / 100
  Contribuicao ao score : {data.get('componente_capacidade', 0):.2f} pts

RESULTADO FINAL
  Score Final    : {data.get('score_final', 0):.2f} / 100
  Classificacao  : {data.get('classificacao', 'N/D')}

{_finalidade_bloco}{_documento_bloco}
CONVENCAO DAS ESCALAS - LEIA ANTES DE REDIGIR
- Serasa (PF e PJ): escala 0-1000. Quanto MAIOR o score, MENOR o risco de inadimplencia.
- Neoway Score de Compliance: escala 0-100. Quanto MENOR o score, MENOR o risco (melhor compliance).
  Valores baixos (ex: 36) indicam BAIXO risco. Valores altos (ex: 85) indicam ALTO risco.
  O sistema INVERTE esse score (100 - score) para normalizar: score 36 -> 64 pts.
- Score final da analise: escala 0-100. Quanto MAIOR, MENOR o risco.
- Pontos normalizados (qualquer pilar): SEMPRE maior = melhor.

Estruture o parecer com os seguintes topicos obrigatorios:
1. RESUMO EXECUTIVO
2. ANALISE DE SAUDE FINANCEIRA / BUREAU
{'3. ANALISE DOS INDICES FINANCEIROS (DRE/Balanco)' if modo_dre else '3. ANALISE DE COMPLIANCE'}
{'4. ANALISE DO BUSINESS PLAN' if modo_bp else ''}
{'5' if modo_bp else '4'}. ANALISE DE CAPACIDADE DE PAGAMENTO
{f"{'6' if modo_bp else '5'}. ANALISE DO DOCUMENTO {_modalidade.upper()} (obrigatorio — analise clausulas, obrigacoes, partes envolvidas, riscos especificos da modalidade)" if _documento_bloco else ''}
{'7' if (modo_bp and _documento_bloco) else '6' if (_documento_bloco or modo_bp) else '5'}. FATORES DE RISCO IDENTIFICADOS
{'8' if (modo_bp and _documento_bloco) else '7' if (_documento_bloco or modo_bp) else '6'}. RECOMENDACAO (Aprovar / Aprovar com Condicionantes / Recusar)
{'9' if (modo_bp and _documento_bloco) else '8' if (_documento_bloco or modo_bp) else '7'}. CONDICIONANTES E MITIGANTES (se aplicavel)

Seja objetivo, tecnico e fundamentado apenas nos dados fornecidos."""

    try:
        client = Anthropic()
        resp = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text
    except Exception as exc:
        return f"Erro ao gerar parecer: {exc}"
