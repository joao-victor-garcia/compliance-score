"""
core/reports.py
Geracao de relatorios para download: TXT e PDF (via ReportLab).
"""

import re
from datetime import datetime
from io import BytesIO
from typing import Optional

from core.ai_engine import BP_DIMENSOES

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


# ── Markdown -> ReportLab ─────────────────────────────────────────────────────

def _md_to_xml(text: str) -> str:
    """
    Converte markdown inline para XML compativel com reportlab Paragraph.
    Sequencia obrigatoria: escape HTML -> bold -> italic.
    """
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.DOTALL)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"\b_(.+?)_\b", r"<i>\1</i>", text)
    return text


def _parecer_to_flowables(parecer, body_s, h2_s, h3_s, navy, teal, bgGray) -> list:
    """
    Converte o parecer em markdown para uma lista de flowables do reportlab.
    Suporta: ## ### headings | **bold** *italic* | listas - | tabelas | | --- separadores.
    """
    bullet_s = ParagraphStyle("BulletParecer", parent=body_s, leftIndent=14, spaceAfter=2, fontSize=9)
    h1_p     = ParagraphStyle("H1Parecer",    parent=h2_s,   fontSize=13, spaceBefore=8, spaceAfter=4)

    flowables = []
    lines = parecer.split("\n")
    i = 0

    while i < len(lines):
        stripped = lines[i].strip()

        if not stripped:
            flowables.append(Spacer(1, 5))
            i += 1
            continue

        if re.match(r"^-{3,}$", stripped):
            flowables += [
                Spacer(1, 4),
                HRFlowable(width="100%", thickness=0.4, color=colors.lightgrey),
                Spacer(1, 4),
            ]
            i += 1
            continue

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

        if stripped.startswith("|"):
            table_lines: list[str] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i].strip())
                i += 1
            data_rows: list[list[str]] = []
            for tl in table_lines:
                if re.match(r"^\|[\s\-:|]+\|$", tl):
                    continue
                cells = [c.strip() for c in tl.strip("|").split("|")]
                data_rows.append(cells)
            if data_rows:
                ncols = max(len(r) for r in data_rows)
                col_width = 440 / ncols
                for r in data_rows:
                    while len(r) < ncols:
                        r.append("")
                para_rows = []
                for ri, row in enumerate(data_rows):
                    cell_style = ParagraphStyle(
                        f"TCell_{ri}", parent=body_s, fontSize=8,
                        fontName="Helvetica-Bold" if ri == 0 else "Helvetica",
                    )
                    para_rows.append([Paragraph(_md_to_xml(c), cell_style) for c in row])
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

        if re.match(r"^[-\*]\s", stripped):
            flowables.append(Paragraph(f"&bull; {_md_to_xml(stripped[2:])}", bullet_s))
            i += 1
            continue

        flowables.append(Paragraph(_md_to_xml(stripped), body_s))
        i += 1

    return flowables


# ── Relatorio TXT ─────────────────────────────────────────────────────────────

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

    def fv(v): return f"{v:,.2f}" if v is not None else "Nao extraido"

    # Composicao do score
    if modo and modo_bp_r:
        comp_bloco = (
            "COMPOSICAO DO SCORE FINAL (Metodologia DRE + Business Plan)\n"
            f"  Bureau  (Serasa+Neoway) (20%) : {data.get('componente_bureau', 0):>6.2f} pts\n"
            f"  Indices Financeiros     (30%) : {data.get('componente_financeiro', 0):>6.2f} pts\n"
            f"  Cap. de Pagamento       (20%) : {data.get('componente_capacidade', 0):>6.2f} pts\n"
            f"  Business Plan           (30%) : {data.get('componente_bp', 0):>6.2f} pts"
        )
    elif modo_bp_r:
        comp_bloco = (
            "COMPOSICAO DO SCORE FINAL (Metodologia com Business Plan)\n"
            f"  Bureau  (Serasa+Neoway) (35%) : {data.get('componente_bureau', 0):>6.2f} pts\n"
            f"  Cap. de Pagamento       (25%) : {data.get('componente_capacidade', 0):>6.2f} pts\n"
            f"  Business Plan           (40%) : {data.get('componente_bp', 0):>6.2f} pts"
        )
    elif modo:
        comp_bloco = (
            "COMPOSICAO DO SCORE FINAL (Metodologia com DRE)\n"
            f"  Bureau  (Serasa+Neoway) (30%) : {data.get('componente_bureau', 0):>6.2f} pts\n"
            f"  Indices Financeiros     (40%) : {data.get('componente_financeiro', 0):>6.2f} pts\n"
            f"  Cap. de Pagamento       (30%) : {data.get('componente_capacidade', 0):>6.2f} pts"
        )
    else:
        comp_bloco = (
            "COMPOSICAO DO SCORE FINAL (Metodologia Padrao)\n"
            f"  Saude Financeira (Serasa)(40%): {data.get('componente_saude', 0):>6.2f} pts\n"
            f"  Compliance (Neoway)      (30%): {data.get('componente_compliance', 0):>6.2f} pts\n"
            f"  Cap. de Pagamento        (30%): {data.get('componente_capacidade', 0):>6.2f} pts"
        )

    # Bloco DRE
    dre_bloco = ""
    if modo and indices:
        lc = indices.get("liquidez_corrente")
        cg = indices.get("cobertura_garantia")
        ml = indices.get("margem_liquida")
        lc_str  = f"{lc:.4f}x"    if lc is not None else "N/D"
        cg_str  = f"{cg:.4f}x"    if cg is not None else "N/D"
        ml_str  = f"{ml*100:.2f}%" if ml is not None else "N/D"
        ptl = indices.get("pts_liquidez")
        ptc = indices.get("pts_cobertura")
        ptm = indices.get("pts_margem")
        ptf = indices.get("pontos_financeiro")
        ptl_str = f"{ptl:.1f}" if ptl is not None else "N/D"
        ptc_str = f"{ptc:.1f}" if ptc is not None else "N/D"
        ptm_str = f"{ptm:.1f}" if ptm is not None else "N/D"
        ptf_str = f"{ptf:.2f}" if ptf is not None else "N/D"
        dre_bloco = (
            "\nINDICES FINANCEIROS (DRE / BALANCO)\n"
            f"  Ativo Circulante    : R$ {fv(data.get('ativo_circulante'))}\n"
            f"  Passivo Circulante  : R$ {fv(data.get('passivo_circulante'))}\n"
            f"  Estoque             : R$ {fv(data.get('estoque'))}\n"
            f"  Patrimonio Liquido  : R$ {fv(data.get('patrimonio_liquido'))}\n"
            f"  Receita Liquida     : R$ {fv(data.get('receita_liquida'))}\n"
            f"  Lucro Liquido       : R$ {fv(data.get('lucro_liquido'))}\n"
            f"  EBITDA              : R$ {fv(data.get('ebitda'))}\n"
            f"  Liquidez Corrente   : {lc_str:>10}  ({ptl_str} pts)\n"
            f"  Cobertura Garantia  : {cg_str:>10}  ({ptc_str} pts)\n"
            f"  Margem Liquida      : {ml_str:>10}  ({ptm_str} pts)\n"
            f"  Pontos Financeiros  : {ptf_str:>10} / 100\n"
        )

    # Bloco Business Plan
    if modo_bp_r and avaliacao_bp:
        _bp_fortes  = "\n".join("  - " + p for p in avaliacao_bp.get("pontos_fortes",  [])) or "  (nenhum)"
        _bp_atencao = "\n".join("  - " + p for p in avaliacao_bp.get("pontos_atencao", [])) or "  (nenhum)"
        bp_bloco = (
            "\nAVALIACAO DO BUSINESS PLAN\n"
            f"  Score Geral           : {avaliacao_bp.get('score_geral', 0):.1f} / 100\n"
            f"  Classificacao         : {avaliacao_bp.get('classificacao', 'N/D')}\n"
            f"  Analise de Mercado    : {avaliacao_bp.get('mercado', {}).get('score', 'N/D')} pts\n"
            f"  Modelo de Negocio     : {avaliacao_bp.get('modelo_negocio', {}).get('score', 'N/D')} pts\n"
            f"  Projecoes Financeiras : {avaliacao_bp.get('projecoes_financeiras', {}).get('score', 'N/D')} pts\n"
            f"  Equipe & Gestao       : {avaliacao_bp.get('equipe_gestao', {}).get('score', 'N/D')} pts\n"
            f"  Estrategia de Risco   : {avaliacao_bp.get('estrategia_risco', {}).get('score', 'N/D')} pts\n"
            f"  Pontos Fortes:\n{_bp_fortes}\n"
            f"  Pontos de Atencao:\n{_bp_atencao}\n"
        )
    else:
        bp_bloco = ""

    # Bloco Finalidade
    if finalidade_text:
        _trunc = finalidade_text[:2000] + ("...[texto truncado]" if len(finalidade_text) > 2000 else "")
        _fin_bloco_txt = "\nFINALIDADE DO USO DO RECURSO\n" + _trunc + "\n"
    else:
        _fin_bloco_txt = ""

    _metodo_label = (
        "COM DRE + Business Plan"              if (modo and modo_bp_r) else
        "COM Business Plan"                    if modo_bp_r else
        "COM Analise Financeira (DRE/Balanco)" if modo else
        "Padrao (Bureau + Capacidade)"
    )

    return (
        "================================================================================\n"
        "         RELATORIO DE ANALISE DE RISCO - GARANTIDORA\n"
        "================================================================================\n"
        f"Emitido em: {ts}\n"
        f"Metodologia: {_metodo_label}\n\n"
        "DADOS DA SOLICITACAO\n"
        f"  Valor da Garantia Solicitada : R$ {data.get('valor_garantia', 0):>14,.2f}\n"
        f"  Tempo de Vigencia            : {data.get('tempo_vigencia', 0)} meses\n\n"
        "BUREAU - SCORES EXTRAIDOS\n"
        f"  Score Serasa PF              : {str(data.get('score_serasa_pf', 'Nao extraido')):>6}  / 1000\n"
        f"  Score Serasa PJ              : {str(data.get('score_serasa_pj', 'Nao extraido')):>6}  / 1000\n"
        f"  Score Compliance Neoway (orig): {str(data.get('score_neoway_original', 'Nao extraido')):>6}  / 100\n"
        f"  Faturamento Estimado         : R$ {fat_min:,.2f} a R$ {fat_max:,.2f}\n"
        f"{dre_bloco}{bp_bloco}{_fin_bloco_txt}\n"
        "CAPACIDADE DE PAGAMENTO\n"
        f"  Faturamento Mensal           : R$ {fat_m:,.2f}\n"
        f"  Capacidade Total no Periodo  : R$ {cap:,.2f}\n"
        f"  Valor da Garantia            : R$ {data.get('valor_garantia', 0):,.2f}\n"
        f"  Situacao                     : {'Suficiente' if cap >= data.get('valor_garantia', 0) else 'Insuficiente'}\n\n"
        f"{comp_bloco}\n"
        "  -------------------------------------\n"
        f"  SCORE FINAL   : {data.get('score_final', 0):>6.2f} / 100\n"
        f"  CLASSIFICACAO : {data.get('classificacao', 'N/D')}\n\n"
        "================================================================================\n"
        " PARECER TECNICO (GERADO POR IA - Anthropic claude-opus-4-6)\n"
        "================================================================================\n\n"
        f"{parecer}\n\n"
        "================================================================================\n"
        " Documento gerado automaticamente - Sistema de Compliance e Risco - Garantidora Premiatto\n"
        "================================================================================\n"
    )


# ── Relatorio PDF ─────────────────────────────────────────────────────────────

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
    doc = SimpleDocTemplate(
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

    title_s = ParagraphStyle("T2",   parent=styles["Title"],   textColor=navy, fontSize=15, spaceAfter=4)
    h2_s    = ParagraphStyle("H2_",  parent=styles["Heading2"], textColor=navy, fontSize=11, spaceAfter=4)
    h3_s    = ParagraphStyle("H3_",  parent=styles["Heading3"], textColor=teal, fontSize=10, spaceAfter=2)
    small_s = ParagraphStyle("Sm_",  parent=styles["Normal"],   textColor=colors.gray, fontSize=8)
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
        Paragraph("RELATORIO DE ANALISE DE RISCO", title_s),
        Paragraph("Sistema de Compliance - Garantidora Premiatto", small_s),
        Paragraph(
            f"Emitido em: {datetime.now().strftime('%d/%m/%Y %H:%M')} · "
            f"Metodologia: {'Com DRE/Balanco' if modo else 'Padrao'}",
            small_s,
        ),
        Spacer(1, 12),
    ]

    fat_min = data.get("faturamento_min") or 0
    fat_max = data.get("faturamento_max") or 0
    story += [
        Paragraph("Dados da Solicitacao", h2_s),
        two_col([
            ["Valor da Garantia", f"R$ {data.get('valor_garantia', 0):,.2f}"],
            ["Tempo de Vigencia", f"{data.get('tempo_vigencia', 0)} meses"],
        ]),
        Spacer(1, 10),
    ]

    story += [
        Paragraph("Bureau - Scores Extraidos", h2_s),
        two_col([
            ["Score Serasa PF",               str(data.get("score_serasa_pf", "N/D"))],
            ["Score Serasa PJ",               str(data.get("score_serasa_pj", "N/D"))],
            ["Score Compliance Neoway (orig)", str(data.get("score_neoway_original", "N/D"))],
            ["Faturamento Min.",              f"R$ {fat_min:,.2f}"],
            ["Faturamento Max.",              f"R$ {fat_max:,.2f}"],
        ]),
        Spacer(1, 10),
    ]

    # Indices financeiros (DRE)
    if modo and indices:
        def fv(v): return f"R$ {v:,.2f}" if v is not None else "N/D"
        def fi(v): return f"{v:.1f} pts" if v is not None else "N/D"
        lc = indices.get("liquidez_corrente")
        cg = indices.get("cobertura_garantia")
        ml = indices.get("margem_liquida")
        story += [
            Paragraph("Analise Financeira - DRE / Balanco", h2_s),
            Paragraph("Contas Extraidas", h3_s),
            two_col([
                ["Ativo Circulante",   fv(data.get("ativo_circulante"))],
                ["Passivo Circulante", fv(data.get("passivo_circulante"))],
                ["Estoque",            fv(data.get("estoque"))],
                ["Patrimonio Liquido", fv(data.get("patrimonio_liquido"))],
                ["Receita Liquida",    fv(data.get("receita_liquida"))],
                ["Lucro Liquido",      fv(data.get("lucro_liquido"))],
                ["EBITDA",             fv(data.get("ebitda"))],
            ]),
            Spacer(1, 6),
            Paragraph("Indices Calculados", h3_s),
        ]
        idx_rows = [
            ["Indice", "Valor Calculado", "Pts (0-100)", "Referencia"],
            ["Liquidez Corrente (40%)",   f"{lc:.4f}x" if lc else "N/D", fi(indices.get("pts_liquidez")),  "Ideal > 1,2"],
            ["Cobertura Garantia (40%)",  f"{cg:.4f}x" if cg else "N/D", fi(indices.get("pts_cobertura")), "EBITDA / Garantia"],
            ["Margem Liquida (20%)",      f"{ml*100:.2f}%" if ml is not None else "N/D", fi(indices.get("pts_margem")), "Lucro / Receita"],
            ["SCORE FINANCEIRO", "",
             f"{indices.get('pontos_financeiro', 0):.2f}" if indices.get("pontos_financeiro") is not None else "N/D", ""],
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
        story += [Paragraph("Finalidade do Uso do Recurso", h2_s), Spacer(1, 4)]
        for linha in finalidade_text[:3000].split("\n"):
            linha = linha.strip()
            if linha:
                story.append(Paragraph(_md_to_xml(linha), body_s))
        story.append(Spacer(1, 10))

    # Business Plan
    if avaliacao_bp:
        def fi(v): return f"{v:.0f} pts" if v is not None else "N/D"
        story += [Paragraph("Avaliacao do Business Plan", h2_s), Spacer(1, 4)]
        bp_score = avaliacao_bp.get("score_geral", 0)
        bp_class = avaliacao_bp.get("classificacao", "")
        bp_color = (
            colors.HexColor("#E53E3E") if bp_score < 40 else
            colors.HexColor("#DD6B20") if bp_score < 60 else
            colors.HexColor("#D69E2E") if bp_score < 75 else
            colors.HexColor("#38A169")
        )
        bp_res_s = ParagraphStyle("BPRes", parent=styles["Heading2"], fontSize=14, textColor=bp_color, spaceAfter=6)
        story.append(Paragraph(f"Score Geral: {bp_score:.1f} / 100 - {bp_class}", bp_res_s))

        dim_rows = [["Dimensao", "Score", "Observacao"]]
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

        pf_list = avaliacao_bp.get("pontos_fortes", [])
        pa_list = avaliacao_bp.get("pontos_atencao", [])
        bullet_bp = ParagraphStyle("BPBullet", parent=body_s, fontSize=8, leftIndent=10)
        if pf_list:
            story.append(Paragraph("<b>Pontos Fortes:</b>", body_s))
            for p in pf_list:
                story.append(Paragraph(f"&bull; {_md_to_xml(p)}", bullet_bp))
        if pa_list:
            story.append(Paragraph("<b>Pontos de Atencao:</b>", body_s))
            for p in pa_list:
                story.append(Paragraph(f"&bull; {_md_to_xml(p)}", bullet_bp))
        story.append(Spacer(1, 10))

    # Composicao do score
    story.append(Paragraph("Composicao do Score Final", h2_s))
    bp_pts_str  = f"{avaliacao_bp.get('score_geral', 0):.1f}" if avaliacao_bp else "N/D"
    fin_pts_str = (f"{indices.get('pontos_financeiro', 0):.1f}"
                   if indices and indices.get("pontos_financeiro") is not None else "N/D")

    if modo and avaliacao_bp:
        comp_rows = [
            ["Componente", "Pts Brutos", "Peso", "Contribuicao"],
            ["Bureau (Serasa + Neoway)", f"{data.get('pontos_bureau', 0):.1f}", "20%", f"{data.get('componente_bureau', 0):.2f}"],
            ["Indices Financeiros (DRE)", fin_pts_str,                          "30%", f"{data.get('componente_financeiro', 0):.2f}"],
            ["Capacidade de Pagamento",  str(data.get("pontos_capacidade", 0)), "20%", f"{data.get('componente_capacidade', 0):.2f}"],
            ["Business Plan (IA)",       bp_pts_str,                            "30%", f"{data.get('componente_bp', 0):.2f}"],
            ["SCORE FINAL", "", "", f"{data.get('score_final', 0):.2f}"],
        ]
    elif avaliacao_bp:
        comp_rows = [
            ["Componente", "Pts Brutos", "Peso", "Contribuicao"],
            ["Bureau (Serasa + Neoway)", f"{data.get('pontos_bureau', 0):.1f}", "35%", f"{data.get('componente_bureau', 0):.2f}"],
            ["Capacidade de Pagamento",  str(data.get("pontos_capacidade", 0)), "25%", f"{data.get('componente_capacidade', 0):.2f}"],
            ["Business Plan (IA)",       bp_pts_str,                            "40%", f"{data.get('componente_bp', 0):.2f}"],
            ["SCORE FINAL", "", "", f"{data.get('score_final', 0):.2f}"],
        ]
    elif modo:
        comp_rows = [
            ["Componente", "Pts Brutos", "Peso", "Contribuicao"],
            ["Bureau (Serasa + Neoway)", f"{data.get('pontos_bureau', 0):.1f}", "30%", f"{data.get('componente_bureau', 0):.2f}"],
            ["Indices Financeiros (DRE)", fin_pts_str,                          "40%", f"{data.get('componente_financeiro', 0):.2f}"],
            ["Capacidade de Pagamento",  str(data.get("pontos_capacidade", 0)), "30%", f"{data.get('componente_capacidade', 0):.2f}"],
            ["SCORE FINAL", "", "", f"{data.get('score_final', 0):.2f}"],
        ]
    else:
        comp_rows = [
            ["Componente", "Pts Brutos", "Peso", "Contribuicao"],
            ["Saude Financeira (Serasa)", f"{data.get('pontos_serasa', 0):.1f}", "40%", f"{data.get('componente_saude', 0):.2f}"],
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

    # Resultado final
    score = data.get("score_final", 0)
    rc = (
        colors.HexColor("#E53E3E") if score < 40 else
        colors.HexColor("#DD6B20") if score < 70 else
        colors.HexColor("#D69E2E") if score < 90 else
        colors.HexColor("#38A169")
    )
    res_s = ParagraphStyle("Res", parent=styles["Heading1"], fontSize=20, textColor=rc, alignment=1, spaceAfter=4)
    story += [
        Paragraph("Resultado Final", h2_s),
        Paragraph(f"{data.get('classificacao', '')} - {score:.2f} / 100", res_s),
        Spacer(1, 12),
    ]

    story.append(Paragraph("Parecer Tecnico (Gerado por IA)", h2_s))
    story.extend(_parecer_to_flowables(parecer, body_s, h2_s, h3_s, navy, teal, bgGray))
    story += [
        Spacer(1, 16),
        Paragraph(
            "Documento gerado automaticamente - Sistema de Compliance e Risco - Garantidora Premiatto",
            small_s,
        ),
    ]

    doc.build(story)
    buffer.seek(0)
    return buffer
