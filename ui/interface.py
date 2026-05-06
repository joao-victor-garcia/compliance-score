"""
ui/interface.py
Interface Streamlit principal: sidebar, pipeline de processamento e exibicao de resultados.
"""

import base64
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import streamlit as st
import streamlit.components.v1 as components

from core.ai_engine import (
    BP_DIMENSOES,
    ANTHROPIC_OK,
    avaliar_business_plan,
    generate_technical_opinion,
)
from core.extractor import (
    extract_dre_balanco,
    extract_neoway_compliance_score,
    extract_neoway_faturamento,
    extract_serasa_score,
    extract_text_from_pdf,
)
from core.reports import REPORTLAB_OK, generate_pdf_report, generate_txt_report
from core.scoring import (
    calculate_final_score_com_bp,
    calculate_final_score_com_dre,
    calculate_final_score_com_dre_bp,
    calculate_final_score_standard,
    calculate_payment_capacity,
    calcular_indices_financeiros,
    get_risk_classification,
)
from ui.auth import is_authenticated, login_page

# CSS do tema light mode
_CSS = """
<style>
.stApp { background: #f0f4f8 !important; }
.block-container { padding-top: 1.5rem !important; }

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
div[data-testid="stButton"] > button div { color: #ffffff !important; }
div[data-testid="stButton"] > button:hover { opacity: 0.88 !important; }

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

[data-testid="stExpander"] {
    background: #ffffff !important;
    border: 1px solid #dde3ed !important;
    border-radius: 10px !important;
    box-shadow: 0 1px 4px rgba(0,0,0,.05) !important;
}
[data-testid="stExpander"] summary { color: #1a365d !important; font-weight: 600 !important; }

[data-testid="stDataFrame"] { border-radius: 8px !important; overflow: hidden !important; }
thead tr th { background: #1a365d !important; color: #ffffff !important; }
tbody tr:nth-child(even) td { background: #f7fafc !important; }

[data-testid="stAlert"] { border-radius: 8px !important; }

.stMarkdown p, .stMarkdown li { color: #2d3748 !important; }
h1, h2, h3 { color: #1a365d !important; }

.header-principal h1,
.header-principal p { color: #ffffff !important; }

.score-card {
    border-radius: 14px; padding: 1.8rem 1rem;
    text-align: center; margin-bottom: 1rem;
    box-shadow: 0 2px 12px rgba(0,0,0,.08);
}

[data-testid="stProgress"] > div > div {
    background: linear-gradient(90deg,#2b6cb0,#4299e1) !important;
    border-radius: 4px !important;
}

[data-testid="stFileUploader"] {
    background: #f7fafc !important;
    border: 1.5px dashed #a0aec0 !important;
    border-radius: 8px !important;
}

[data-testid="stSidebar"] [data-testid="StyledFullScreenButton"],
[data-testid="stSidebar"] [data-testid="stImage"] button,
[data-testid="stSidebar"] .stImage button,
[data-testid="stSidebar"] button[title="View fullscreen"],
[data-testid="stSidebar"] button[title="Fullscreen"] {
    display: none !important;
    visibility: hidden !important;
    opacity: 0 !important;
    pointer-events: none !important;
}

[data-testid="stNumberInput"] input {
    background: #f7fafc !important;
    border: 1px solid #cbd5e0 !important;
    color: #2d3748 !important;
    border-radius: 6px !important;
}
</style>
"""

_DOTS_JS = """
<script>
(function () {
    if (window.parent._stDotsActive) return;
    window.parent._stDotsActive = true;

    var p = window.parent.document;
    var dots = 1;
    var timer = null;

    function tick() {
        var w = p.querySelector('[data-testid="stStatusWidget"]');
        if (!w) return;
        var c = w.querySelector('._stDots');
        if (!c) {
            c = p.createElement('span');
            c.className = '_stDots';
            c.style.cssText = 'font-family:monospace;font-size:15px;color:#4a5568;display:inline-block;min-width:22px;';
            Array.from(w.children).forEach(function (el) {
                el.style.visibility = 'hidden';
                el.style.position = 'absolute';
            });
            w.style.position = 'relative';
            w.appendChild(c);
        }
        c.textContent = '.'.repeat(dots);
        dots = dots % 3 + 1;
    }

    new p.MutationObserver(function () {
        var w = p.querySelector('[data-testid="stStatusWidget"]');
        if (w && !timer) {
            timer = setInterval(tick, 450);
        } else if (!w && timer) {
            clearInterval(timer);
            timer = null;
            dots = 1;
        }
    }).observe(p.body, { childList: true, subtree: true });
})();
</script>
"""


def _get_logo_png() -> Optional[str]:
    """Converte Logo_GPRemiatto.pdf -> PNG na pasta assets e retorna o caminho."""
    assets_dir = Path(__file__).parent.parent / "assets"
    png_path   = assets_dir / "logo_premiatto.png"
    pdf_path   = assets_dir / "Logo_GPRemiatto.pdf"

    if png_path.exists():
        return str(png_path)
    if pdf_path.exists():
        try:
            import fitz
            doc  = fitz.open(str(pdf_path))
            page = doc[0]
            mat  = fitz.Matrix(3, 3)
            pix  = page.get_pixmap(matrix=mat, alpha=False)
            pix.save(str(png_path))
            doc.close()
            return str(png_path)
        except Exception:
            pass
    return None


def main() -> None:
    st.set_page_config(
        page_title="Compliance & Risco - Garantidora Premiatto",
        page_icon="🏦",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    logo_png = _get_logo_png()

    if not is_authenticated():
        login_page(logo_png)
        st.stop()

    st.markdown(_CSS, unsafe_allow_html=True)
    components.html(_DOTS_JS, height=0, scrolling=False)

    st.markdown("""
    <div class="header-principal" style="background:linear-gradient(135deg,#1a365d,#2b6cb0);
                border-radius:14px; padding:1.4rem 2rem; margin-bottom:1.5rem;
                box-shadow:0 4px 16px rgba(26,54,93,0.18);">
        <h1 style="color:#ffffff !important;margin:0;font-size:1.75rem;font-weight:800;">
            Sistema de Compliance e  Análise de Risco
        </h1>
        <p style="color:#ffffff !important;margin:0.3rem 0 0;font-size:0.95rem;">
            Análise automatizada de garantias · Garantidora Premiatto
        </p>
    </div>
    """, unsafe_allow_html=True)

    # ── Sidebar ───────────────────────────────────────────────────────────────
    with st.sidebar:
        if logo_png:
            _img_b64 = base64.b64encode(Path(logo_png).read_bytes()).decode()
            st.markdown(
                f'<img src="data:image/png;base64,{_img_b64}" style="width:100%;display:block;">',
                unsafe_allow_html=True,
            )
            st.markdown("---")

        user_email = st.session_state.get("user_email", "")
        st.markdown(
            f"<small style='color:#718096;'>Logado como<br><b>{user_email}</b></small>",
            unsafe_allow_html=True,
        )
        if st.button("Sair", use_container_width=True):
            st.session_state.clear()
            st.rerun()

        st.markdown("---")
        st.markdown("### 👤 Identificação do Solicitante")
        nome_solicitante = st.text_input(
            "Nome da PF / PJ",
            placeholder="Ex: João da Silva ou Empresa Ltda",
        )

        tipo_pessoa = st.radio(
            "Selecione o tipo de análise",
            options=["Pessoa Física", "Pessoa Jurídica"],
            horizontal=True,
            label_visibility="collapsed",
        )
        is_pj = tipo_pessoa == "Pessoa Jurídica"

        st.markdown("---")
        st.markdown("### 📂 Relatórios")
        st.markdown("---")
        serasa_pf_file = st.file_uploader("Serasa PF - Pessoa Fisica",  type=["pdf"])
        serasa_pj_file = st.file_uploader("Serasa PJ - Pessoa Juridica", type=["pdf"]) if is_pj else None
        neoway_file    = st.file_uploader("Due Diligence Neoway",         type=["pdf"])

        st.markdown("---")
        st.markdown("### 📊 Documentos Adicionais *(opcionais)*")
        dre_file = st.file_uploader(
            "Balanco Patrimonial / DRE", type=["pdf"],
            help="Bureau 30% | Financeiro 40% | Capacidade 30%",
        )
        if dre_file:
            st.success("DRE/Balanco carregado")

        bp_file = st.file_uploader(
            "Business Plan", type=["pdf"],
            help="Sem DRE: Bureau 35% | Cap 25% | BP 40%\nCom DRE: Bureau 20% | DRE 30% | Cap 20% | BP 30%",
        )
        if bp_file:
            st.success("Business Plan carregado")

        finalidade_file = st.file_uploader(
            "Finalidade do Recurso", type=["pdf"],
            help="Contexto qualitativo para o parecer da IA.",
        )
        if finalidade_file:
            st.success("Finalidade do recurso carregada")

        st.markdown("---")
        st.markdown("### 💰 Parametros da Garantia")

        modalidade = st.selectbox(
            "Modalidade",
            options=["Financeira", "Processual", "Contratual"],
            help="Financeira: análise padrão. Processual/Contratual: exige documento do processo ou contrato.",
        )

        documento_modal_file = None
        if modalidade in ("Processual", "Contratual"):
            _label_doc = "Documento do Processo (PDF) *" if modalidade == "Processual" else "Documento do Contrato (PDF) *"
            documento_modal_file = st.file_uploader(_label_doc, type=["pdf"])
            if documento_modal_file:
                st.success(f"Documento {modalidade.lower()} carregado")
            else:
                st.warning(f"⚠️ Documento obrigatório para modalidade {modalidade}.")

        def _parse_br(s: str) -> float:
            return float(s.replace(".", "").replace(",", "."))

        def _fmt_valor():
            val = st.session_state.get("_valor_str", "").strip()
            try:
                num = int(_parse_br(val))
            except (ValueError, AttributeError):
                digits = re.sub(r"[^\d]", "", val)
                num = int(digits) if digits else 0
            if num >= 0:
                st.session_state["_valor_str"] = f"{num:,}".replace(",", ".") + ",00"

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
            "Tempo de Vigencia (meses)",
            min_value=1, max_value=360, value=12, step=1,
        )

        st.markdown("---")
        processar = st.button("Processar e Calcular risco")

    # ── Estado inicial ────────────────────────────────────────────────────────
    if not processar:
        c1, c2, c3 = st.columns(3)
        c1.info("**1.** Faca upload dos PDFs no painel lateral")
        c2.info("**2.** Informe modalidade, valor e vigência da garantia")
        c3.info("**3.** Clique em **Processar e Calcular Risco**")

        with st.expander("Metodologia de Cálculo"):
            st.markdown("""
**Modo Padrao** (sem DRE):

| Componente | Fonte | Peso | Normalização |
|---|---|---|---|
| Saude Financeira | Serasa PF/PJ | **40 %** | `Score / 10` |
| Compliance | Neoway | **30 %** | `100 - Score` (inversao) |
| Capacidade de Pagamento | Neoway (Faturamento) | **30 %** | 100 se capaz; 0 se nao |

---

**Modo Analise Financeira** (com DRE/Balanço):

| Componente | Fonte | Peso |
|---|---|---|
| Bureau (Serasa + Neoway) | PDFs Bureau | **30 %** |
| Indices Financeiros | DRE / Balanco | **40 %** |
| Capacidade de Pagamento | Neoway (Faturamento) | **30 %** |

**Classificação de Risco:**

| Score | Nivel |
|---|---|
| 90-100 | 🟢 Risco Minimo |
| 70-89  | 🟡 Risco Moderado |
| 40-69  | 🟠 Risco Moderado-Alto |
| 0-39   | 🔴 Risco Alto |
""")
        return

    # ── Pipeline de processamento ─────────────────────────────────────────────
    prog = st.progress(0)
    msg  = st.empty()

    _step = [0]

    def _loading(text: str) -> None:
        dots = "." * (_step[0] % 3 + 1)
        msg.markdown(f"**{text}**{dots}")
        _step[0] += 1

    results: dict = {
        "tipo_pessoa":           tipo_pessoa,
        "nome_solicitante":      nome_solicitante.strip() or "Sem Nome",
        "modalidade":            modalidade,
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

    _loading("Extraindo Serasa PF")
    prog.progress(8)
    if serasa_pf_file:
        txt = extract_text_from_pdf(serasa_pf_file)
        results["score_serasa_pf"] = extract_serasa_score(txt)

    _loading("Extraindo Serasa PJ")
    prog.progress(18)
    if serasa_pj_file:
        txt = extract_text_from_pdf(serasa_pj_file)
        results["score_serasa_pj"] = extract_serasa_score(txt)

    _loading("Extraindo Neoway Due Diligence")
    prog.progress(30)
    if neoway_file:
        txt = extract_text_from_pdf(neoway_file)
        results["score_neoway_original"]                       = extract_neoway_compliance_score(txt)
        results["faturamento_min"], results["faturamento_max"] = extract_neoway_faturamento(txt)

    if dre_file:
        _loading("Extraindo DRE / Balanco Patrimonial")
        prog.progress(45)
        txt_dre   = extract_text_from_pdf(dre_file)
        dados_dre = extract_dre_balanco(txt_dre)
        for k, v in dados_dre.items():
            results[k] = v

        _loading("Calculando indices financeiros")
        prog.progress(55)
        indices = calcular_indices_financeiros(dados_dre, valor_garantia)

        extraidos     = [k for k, v in dados_dre.items() if v is not None]
        nao_extraidos = [k for k, v in dados_dre.items() if v is None]
        if extraidos:
            st.info(f"DRE/Balanco: extraidos {len(extraidos)}/7 campos - {', '.join(extraidos)}")
        if nao_extraidos:
            st.warning(f"Campos nao extraidos do DRE: **{', '.join(nao_extraidos)}**. Verifique se o PDF contem texto pesquisavel.")

    if bp_file:
        _loading("Avaliando Business Plan com IA")
        prog.progress(58)
        txt_bp = extract_text_from_pdf(bp_file)
        if txt_bp:
            avaliacao_bp = avaliar_business_plan(txt_bp, valor_garantia, tempo_vigencia)
            if avaliacao_bp:
                st.info(f"Business Plan avaliado: **{avaliacao_bp.get('score_geral', 0):.1f}/100** - {avaliacao_bp.get('classificacao', '')}")
            else:
                st.warning("Nao foi possivel avaliar o Business Plan via IA.")
        else:
            st.warning("Business Plan: PDF sem texto extraivel.")
        results["modo_bp"] = avaliacao_bp is not None

    finalidade_text: Optional[str] = None
    if finalidade_file:
        _loading("Extraindo finalidade do recurso")
        prog.progress(62)
        finalidade_text = extract_text_from_pdf(finalidade_file)
        if finalidade_text:
            st.info("Finalidade do recurso extraida e sera usada como contexto no parecer.")
        else:
            st.warning("Finalidade do Recurso: PDF sem texto extraivel.")

    documento_modal_text: Optional[str] = None
    if documento_modal_file:
        _loading(f"Extraindo documento {modalidade.lower()}")
        prog.progress(64)
        documento_modal_text = extract_text_from_pdf(documento_modal_file)
        if documento_modal_text:
            st.info(f"Documento {modalidade} extraido e sera analisado no parecer.")
        else:
            st.warning(f"Documento {modalidade}: PDF sem texto extraivel.")

    _loading("Normalizando scores de bureau")
    prog.progress(65)
    serasa_vals = [v for v in [results["score_serasa_pf"], results["score_serasa_pj"]] if v is not None]
    if serasa_vals:
        results["pontos_serasa"] = round(max(serasa_vals) / 10, 2)
    else:
        st.warning("Score Serasa nao encontrado. Atribuindo 0 pts.")

    if results["score_neoway_original"] is not None:
        results["pontos_neoway"] = round(100 - results["score_neoway_original"], 2)
    else:
        st.warning("Score Neoway nao encontrado. Atribuindo 0 pts.")

    _loading("Calculando capacidade de pagamento")
    prog.progress(72)
    results["pontos_capacidade"] = calculate_payment_capacity(
        results["faturamento_max"], valor_garantia, tempo_vigencia,
    )
    if results["faturamento_max"] is None:
        st.warning("Faturamento nao encontrado. Atribuindo 0 pts.")

    _loading("Calculando score final")
    prog.progress(84)

    ps, pn = results["pontos_serasa"], results["pontos_neoway"]
    if ps > 0 and pn > 0:
        pontos_bureau_combinado = round((ps + pn) / 2, 2)
    elif ps > 0:
        pontos_bureau_combinado = ps
    elif pn > 0:
        pontos_bureau_combinado = pn
    else:
        pontos_bureau_combinado = 0.0

    tem_dre   = results["modo_dre"] and indices and indices.get("pontos_financeiro") is not None
    tem_bp    = avaliacao_bp is not None
    pontos_bp = float(avaliacao_bp["score_geral"]) if tem_bp else 0.0

    if tem_dre and tem_bp:
        pf = indices["pontos_financeiro"]
        results.update({
            "pontos_bureau":         pontos_bureau_combinado,
            "pontos_financeiro":     pf,
            "pontos_bp":             pontos_bp,
            "componente_bureau":     round(pontos_bureau_combinado * 0.20, 2),
            "componente_financeiro": round(pf                      * 0.30, 2),
            "componente_capacidade": round(results["pontos_capacidade"] * 0.20, 2),
            "componente_bp":         round(pontos_bp              * 0.30, 2),
            "score_final":           calculate_final_score_com_dre_bp(
                pontos_bureau_combinado, pf, results["pontos_capacidade"], pontos_bp),
        })
    elif tem_bp:
        results.update({
            "pontos_bureau":         pontos_bureau_combinado,
            "pontos_bp":             pontos_bp,
            "componente_bureau":     round(pontos_bureau_combinado       * 0.35, 2),
            "componente_capacidade": round(results["pontos_capacidade"] * 0.25, 2),
            "componente_bp":         round(pontos_bp                    * 0.40, 2),
            "score_final":           calculate_final_score_com_bp(
                pontos_bureau_combinado, results["pontos_capacidade"], pontos_bp),
        })
        results["modo_dre"] = False
    elif tem_dre:
        pf = indices["pontos_financeiro"]
        results.update({
            "pontos_bureau":         pontos_bureau_combinado,
            "pontos_financeiro":     pf,
            "componente_bureau":     round(pontos_bureau_combinado * 0.30, 2),
            "componente_financeiro": round(pf                      * 0.40, 2),
            "componente_capacidade": round(results["pontos_capacidade"] * 0.30, 2),
            "score_final":           calculate_final_score_com_dre(
                pontos_bureau_combinado, pf, results["pontos_capacidade"]),
        })
    else:
        results.update({
            "componente_saude":      round(results["pontos_serasa"]     * 0.40, 2),
            "componente_compliance": round(results["pontos_neoway"]     * 0.30, 2),
            "componente_capacidade": round(results["pontos_capacidade"] * 0.30, 2),
            "score_final":           calculate_final_score_standard(
                results["pontos_serasa"], results["pontos_neoway"], results["pontos_capacidade"]),
        })
        if results["modo_dre"]:
            st.warning("DRE carregado mas sem indices calculaveis. Usando metodologia padrao.")
            results["modo_dre"] = False

    label, cor, _ = get_risk_classification(results["score_final"])
    emoji_map = {"Risco Alto": "🔴", "Risco Moderado-Alto": "🟠", "Risco Moderado": "🟡", "Risco Minimo": "🟢"}
    results["classificacao"] = f"{emoji_map.get(label, '')} {label}"
    results["cor"]           = cor

    _loading("Gerando parecer tecnico com IA")
    prog.progress(93)
    parecer = generate_technical_opinion(results, indices, avaliacao_bp, finalidade_text, documento_modal_text)

    prog.progress(100)
    msg.markdown("**Analise concluida!**")
    time.sleep(0.8)
    prog.empty()
    msg.empty()

    # ── Exibicao dos Resultados ───────────────────────────────────────────────
    _render_results(results, indices, avaliacao_bp, finalidade_text, parecer,
                    tem_dre, tem_bp, valor_garantia, tempo_vigencia,
                    results["nome_solicitante"])


def _render_results(
    results, indices, avaliacao_bp, finalidade_text, parecer,
    tem_dre, tem_bp, valor_garantia, tempo_vigencia, nome_solicitante="",
):
    """Renderiza a secao de resultados apos o processamento."""
    st.markdown("## 📊 Resultado da Analise")
    modo_dre = results["modo_dre"]

    # Cabeçalho — Dados da Solicitação
    _modalidade = results.get("modalidade", "Financeira")
    _tipo_p     = results.get("tipo_pessoa", "")
    _vg         = results.get("valor_garantia", 0)
    _tv         = results.get("tempo_vigencia", 0)
    _modal_icons = {"Financeira": "💵", "Processual": "⚖️", "Contratual": "📝"}
    _modal_icon  = _modal_icons.get(_modalidade, "")
    st.markdown(f"""
    <div style="background:#ffffff;border:1px solid #dde3ed;border-radius:10px;
                padding:1rem 1.5rem;margin-bottom:1rem;
                box-shadow:0 1px 4px rgba(0,0,0,.05);">
        <div style="font-size:0.7rem;color:#718096;font-weight:700;
                    text-transform:uppercase;letter-spacing:0.06em;margin-bottom:0.5rem;">
            Dados da Solicitação
        </div>
        <div style="display:flex;gap:2.5rem;flex-wrap:wrap;align-items:center;">
            <div>
                <span style="font-size:0.75rem;color:#718096;">Tipo de Pessoa</span><br>
                <span style="font-weight:700;color:#1a365d;">{_tipo_p}</span>
            </div>
            <div>
                <span style="font-size:0.75rem;color:#718096;">Modalidade</span><br>
                <span style="font-weight:700;color:#1a365d;">{_modal_icon} {_modalidade}</span>
            </div>
            <div>
                <span style="font-size:0.75rem;color:#718096;">Valor Solicitado</span><br>
                <span style="font-weight:700;color:#1a365d;">R$ {_vg:,.2f}</span>
            </div>
            <div>
                <span style="font-size:0.75rem;color:#718096;">Vigência</span><br>
                <span style="font-weight:700;color:#1a365d;">{_tv} meses</span>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    if tem_dre and tem_bp:
        st.info("**Metodologia Completa ativa** - DRE + Business Plan: Bureau 20% | Fin 30% | Cap 20% | BP 30%")
    elif tem_bp:
        st.info("**Metodologia com Business Plan ativa**: Bureau 35% | Capacidade 25% | Business Plan 40%")
    elif modo_dre:
        st.info("**Metodologia Avancada ativa** - DRE/Balanco incluido: Bureau 30% | Fin 40% | Cap 30%")
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

    # Colunas de detalhe
    _ncols = 4 if (tem_dre and tem_bp) else 3
    cols = st.columns(_ncols)

    with cols[0]:
        if modo_dre or tem_bp:
            _bur_peso = "20%" if (tem_dre and tem_bp) else ("35%" if tem_bp else "30%")
            st.markdown("#### 📋 Bureau (Serasa + Neoway)")
            st.metric(f"Pontos (peso {_bur_peso})", f"{results.get('pontos_bureau', 0):.1f} / 100",
                      delta=f"Contribuicao: {results.get('componente_bureau', 0):.2f} pts")
            pf = results["score_serasa_pf"]
            pj = results["score_serasa_pj"]
            orig = results["score_neoway_original"]
            st.markdown(f"- Serasa PF: **{pf if pf is not None else 'N/D'}** / 1000")
            st.markdown(f"- Serasa PJ: **{pj if pj is not None else 'N/D'}** / 1000")
            st.markdown(f"- Neoway (orig): **{orig if orig is not None else 'N/D'}** / 100")
        else:
            st.markdown("#### 💰 Saude Financeira")
            st.metric("Pontos (peso 40%)", f"{results['pontos_serasa']:.1f} / 100",
                      delta=f"Contribuicao: {results['componente_saude']:.2f} pts")
            pf = results["score_serasa_pf"]
            pj = results["score_serasa_pj"]
            st.markdown(f"- Serasa PF: **{pf if pf is not None else 'N/D'}** / 1000")
            st.markdown(f"- Serasa PJ: **{pj if pj is not None else 'N/D'}** / 1000")

    with cols[1]:
        if tem_dre:
            _fin_peso = "30%" if tem_bp else "40%"
            st.markdown("#### 📊 Indices Financeiros (DRE)")
            pf_pts = results.get("pontos_financeiro", 0) or 0
            st.metric(f"Pontos (peso {_fin_peso})", f"{pf_pts:.1f} / 100",
                      delta=f"Contribuicao: {results.get('componente_financeiro', 0):.2f} pts")
            if indices:
                lc = indices.get("liquidez_corrente")
                cg = indices.get("cobertura_garantia")
                ml = indices.get("margem_liquida")
                st.markdown(f"- Liquidez Corrente: **{f'{lc:.2f}x' if lc is not None else 'N/D'}**")
                st.markdown(f"- Cob. Garantia: **{f'{cg:.2f}x' if cg is not None else 'N/D'}**")
                st.markdown(f"- Margem Liquida: **{f'{ml*100:.1f}%' if ml is not None else 'N/D'}**")
        elif tem_bp:
            st.markdown("#### 📈 Capacidade de Pagamento")
            st.metric("Pontos (peso 25%)", f"{results['pontos_capacidade']} / 100",
                      delta=f"Contribuicao: {results['componente_capacidade']:.2f} pts")
            _render_capacidade(results, tempo_vigencia, valor_garantia)
        else:
            st.markdown("#### 🛡️ Compliance")
            st.metric("Pontos (peso 30%)", f"{results['pontos_neoway']:.1f} / 100",
                      delta=f"Contribuicao: {results['componente_compliance']:.2f} pts")
            orig = results["score_neoway_original"]
            st.markdown(f"- Score Neoway orig.: **{orig if orig is not None else 'N/D'}**")
            st.markdown(f"- Normalizado (inv.): **{results['pontos_neoway']:.1f}**")

    with cols[2]:
        if tem_dre and tem_bp:
            st.markdown("#### 📈 Capacidade de Pagamento")
            st.metric("Pontos (peso 20%)", f"{results['pontos_capacidade']} / 100",
                      delta=f"Contribuicao: {results['componente_capacidade']:.2f} pts")
            _render_capacidade(results, tempo_vigencia, valor_garantia)
        elif tem_bp:
            _render_bp_col(avaliacao_bp, results, "40%")
        else:
            _cap_peso = "30%"
            st.markdown("#### 📈 Capacidade de Pagamento")
            st.metric(f"Pontos (peso {_cap_peso})", f"{results['pontos_capacidade']} / 100",
                      delta=f"Contribuicao: {results['componente_capacidade']:.2f} pts")
            _render_capacidade(results, tempo_vigencia, valor_garantia)

    if tem_dre and tem_bp:
        with cols[3]:
            _render_bp_col(avaliacao_bp, results, "30%")

    # Detalhamento DRE
    if modo_dre and any(results.get(k) for k in ["ativo_circulante", "passivo_circulante", "receita_liquida", "lucro_liquido", "ebitda"]):
        st.markdown("---")
        with st.expander("📑 Detalhamento - Contas DRE / Balanco Extraidas", expanded=True):
            def fv(v): return f"R$ {v:,.2f}" if v is not None else "-"
            d1, d2 = st.columns(2)
            with d1:
                st.markdown("**Balanco Patrimonial**")
                st.markdown(f"- Ativo Circulante: **{fv(results.get('ativo_circulante'))}**")
                st.markdown(f"- Passivo Circulante: **{fv(results.get('passivo_circulante'))}**")
                st.markdown(f"- Estoque: **{fv(results.get('estoque'))}**")
                st.markdown(f"- Patrimonio Liquido: **{fv(results.get('patrimonio_liquido'))}**")
            with d2:
                st.markdown("**DRE**")
                st.markdown(f"- Receita Liquida: **{fv(results.get('receita_liquida'))}**")
                st.markdown(f"- Lucro Liquido: **{fv(results.get('lucro_liquido'))}**")
                st.markdown(f"- EBITDA / LAJIDA: **{fv(results.get('ebitda'))}**")

    # Detalhamento Business Plan
    if tem_bp and avaliacao_bp:
        st.markdown("---")
        with st.expander("📑 Detalhamento - Avaliacao do Business Plan", expanded=True):
            bp_cols = st.columns([2, 1])
            with bp_cols[0]:
                st.markdown("**Pontuacao por Dimensao**")
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
                st.markdown(f"**Classificacao:** {bp_class}")

            pts_fortes  = avaliacao_bp.get("pontos_fortes",  [])
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
                        st.markdown("**Pontos de Atencao**")
                        for p in pts_atencao:
                            st.markdown(f"- ⚠️ {p}")

    # Composicao do Score
    st.markdown("---")
    st.markdown("### 🧮 Composicao Detalhada do Score")
    try:
        import pandas as pd
        if tem_dre and tem_bp:
            pf_pts_val   = indices.get("pontos_financeiro", 0) if indices else 0
            bp_geral_val = avaliacao_bp.get("score_geral", 0) if avaliacao_bp else 0
            df = pd.DataFrame({
                "Componente":   ["Bureau (Serasa+Neoway)", "Indices Financeiros (DRE)", "Cap. de Pagamento", "Business Plan"],
                "Pts Brutos":   [results.get("pontos_bureau", 0), pf_pts_val or 0, results["pontos_capacidade"], bp_geral_val],
                "Peso":         ["20%", "30%", "20%", "30%"],
                "Contribuicao": [results.get("componente_bureau", 0), results.get("componente_financeiro", 0),
                                 results["componente_capacidade"], results.get("componente_bp", 0)],
            })
        elif tem_bp:
            bp_geral_val = avaliacao_bp.get("score_geral", 0) if avaliacao_bp else 0
            df = pd.DataFrame({
                "Componente":   ["Bureau (Serasa+Neoway)", "Cap. de Pagamento", "Business Plan"],
                "Pts Brutos":   [results.get("pontos_bureau", 0), results["pontos_capacidade"], bp_geral_val],
                "Peso":         ["35%", "25%", "40%"],
                "Contribuicao": [results.get("componente_bureau", 0), results["componente_capacidade"], results.get("componente_bp", 0)],
            })
        elif modo_dre:
            pf_pts_val = indices.get("pontos_financeiro", 0) if indices else 0
            df = pd.DataFrame({
                "Componente":   ["Bureau (Serasa+Neoway)", "Indices Financeiros (DRE)", "Cap. de Pagamento"],
                "Pts Brutos":   [results.get("pontos_bureau", 0), pf_pts_val or 0, results["pontos_capacidade"]],
                "Peso":         ["30%", "40%", "30%"],
                "Contribuicao": [results.get("componente_bureau", 0), results.get("componente_financeiro", 0), results["componente_capacidade"]],
            })
        else:
            df = pd.DataFrame({
                "Componente":   ["Saude Financeira (Serasa)", "Compliance (Neoway)", "Cap. de Pagamento"],
                "Pts Brutos":   [results["pontos_serasa"], results["pontos_neoway"], results["pontos_capacidade"]],
                "Peso":         ["40%", "30%", "30%"],
                "Contribuicao": [results["componente_saude"], results["componente_compliance"], results["componente_capacidade"]],
            })
        st.dataframe(df, use_container_width=True, hide_index=True)
    except ImportError:
        st.write("Instale pandas: `pip install pandas`")

    # Parecer
    st.markdown("---")
    st.markdown("### 🤖 Parecer Tecnico (Gerado por IA)")
    with st.expander("Ver parecer completo", expanded=True):
        st.markdown(parecer)

    # Downloads
    st.markdown("---")
    st.markdown("### 📥 Download do Relatorio")
    _nome_arquivo = re.sub(r'[\\/*?:"<>|]', "", nome_solicitante).strip() or "Sem Nome"
    _base_nome = f"Relatório de Compliance externo - {_nome_arquivo}"

    dl1, dl2 = st.columns(2)
    with dl1:
        txt_report = generate_txt_report(results, parecer, indices, avaliacao_bp, finalidade_text)
        st.download_button(
            "📄 Baixar Relatorio (.txt)", data=txt_report,
            file_name=f"{_base_nome}.txt",
            mime="text/plain", use_container_width=True,
        )
    with dl2:
        if REPORTLAB_OK:
            pdf_buf = generate_pdf_report(results, parecer, indices, avaliacao_bp, finalidade_text)
            if pdf_buf:
                st.download_button(
                    "📑 Baixar Relatorio (.pdf)", data=pdf_buf,
                    file_name=f"{_base_nome}.pdf",
                    mime="application/pdf", use_container_width=True,
                )
        else:
            st.info("Para PDF: `pip install reportlab`")

    # Debug
    with st.expander("🔍 Debug - dados brutos"):
        col_r, col_i, col_bp = st.columns(3)
        with col_r:
            st.markdown("**Results**")
            st.json({k: v for k, v in results.items() if k != "cor"})
        with col_i:
            st.markdown("**Indices DRE**")
            st.json(indices or {})
        with col_bp:
            st.markdown("**Business Plan**")
            st.json(avaliacao_bp or {})


def _render_capacidade(results, tempo_vigencia, valor_garantia):
    fat_max = results["faturamento_max"]
    if fat_max:
        fat_m = fat_max / 12
        cap   = fat_m * tempo_vigencia
        st.markdown(f"- Faturamento Mensal: **R$ {fat_m:,.2f}**")
        st.markdown(f"- Capacidade: **R$ {cap:,.2f}**")
        ok = "✅ Suficiente" if cap >= valor_garantia else "❌ Insuficiente"
        st.markdown(f"- Situacao: **{ok}**")
    else:
        st.error("Faturamento nao extraido")


def _render_bp_col(avaliacao_bp, results, peso):
    st.markdown("#### 📑 Business Plan")
    bp_geral = avaliacao_bp.get("score_geral", 0) if avaliacao_bp else 0
    st.metric(
        f"Pontos (peso {peso})", f"{bp_geral:.1f} / 100",
        delta=f"Contribuicao: {results.get('componente_bp', 0):.2f} pts",
    )
    if avaliacao_bp:
        st.markdown(f"- Classificacao: **{avaliacao_bp.get('classificacao', 'N/D')}**")
        for dim_key, dim_label in BP_DIMENSOES.items():
            d = avaliacao_bp.get(dim_key, {})
            sc = d.get("score", "N/D")
            st.markdown(f"- {dim_label}: **{sc}/100**")
