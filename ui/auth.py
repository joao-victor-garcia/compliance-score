"""
ui/auth.py
Autenticacao: hash de senha, tela de login e verificacao de sessao.
"""

import hashlib
from typing import Optional

import streamlit as st

_CREDENTIALS = {
    "compliance.joaogarcia@bancopremiatto.com.br":
        "6c95e1a64c36008239a4d9bedbb674b38bef6c59f7bcb922b6a51a0849a94df6",
}


def _hash(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def is_authenticated() -> bool:
    return bool(st.session_state.get("authenticated"))


def login_page(logo_png: Optional[str]) -> None:
    """Renderiza a tela de login e gerencia o estado de autenticacao."""
    st.markdown("""
    <style>
    .stApp { background: #f0f4f8 !important; }
    </style>
    """, unsafe_allow_html=True)

    _, col, _ = st.columns([1, 2, 1])
    with col:
        if logo_png:
            st.image(logo_png, use_container_width=True)
        else:
            st.markdown(
                "<h2 style='text-align:center;color:#1a365d;'>Garantidora Premiatto</h2>",
                unsafe_allow_html=True,
            )

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
