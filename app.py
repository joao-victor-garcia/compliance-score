"""
Sistema de Compliance e Risco - Garantidora Premiatto

Uso:
    streamlit run app.py

Estrutura do projeto:
    core/extractor.py  -- extracao de texto de PDFs e parsing bureau/DRE
    core/scoring.py    -- motor de score e indices financeiros
    core/ai_engine.py  -- avaliacao do Business Plan e parecer tecnico (IA)
    core/reports.py    -- geracao de relatorios TXT e PDF
    ui/auth.py         -- autenticacao (login/logout)
    ui/interface.py    -- interface Streamlit principal
"""

from ui.interface import main

main()
