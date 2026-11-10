"""Shared visual shell for Mirum's engineering workflows."""
from html import escape
from pathlib import Path

import streamlit as st


WORKFLOWS = {
    "cad": ("Text to CAD", "GENERATIVE DESIGN", "Describe a part. Generate and inspect its geometry."),
    "dragster": ("Dragster", "PARAMETRIC DESIGN", "Wedge body geometry, dimensions and STEP checks."),
    "glider": ("Glider Search", "DESIGN EXPLORATION", "Search configurations or explore a single glider."),
    "engineering": ("Glider Engineering", "MISSION DESIGN", "Define a mission. Compare processes and inspect the physics."),
}

_MARK = '''<svg width="32" height="32" viewBox="0 0 32 32" fill="none" aria-hidden="true">
<path d="M4 25V7l12 12L28 7v18" stroke="#628cff" stroke-width="2.5" stroke-linejoin="round"/>
<path d="m4 15 12 12 12-12" stroke="#628cff" stroke-width="1.3" opacity=".55"/>
</svg>'''


def apply_workspace() -> None:
    """Load local, static styles; no user content is interpolated into CSS."""
    css = Path(__file__).with_name("workspace.css").read_text(encoding="utf-8")
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def render_navigation() -> str:
    """Keep a stable workflow ID across ordinary control reruns."""
    key = "workspace_workflow"
    if st.session_state.get(key) not in WORKFLOWS:
        st.session_state[key] = "cad"
    with st.sidebar:
        st.markdown(f'<div class="mirum-brand">{_MARK}<span>Mirum</span></div>', unsafe_allow_html=True)
        st.markdown('<div class="nav-label">DESIGN WORKSPACE</div>', unsafe_allow_html=True)
        selected = st.radio("Design workflow", list(WORKFLOWS), key=key,
                            format_func=lambda value: WORKFLOWS[value][0], label_visibility="collapsed")
        st.markdown('<div class="sidebar-note"><span class="sidebar-rule"></span>'
                    'GEOMETRY &amp; ENGINEERING<br><small>Research workspace</small></div>',
                    unsafe_allow_html=True)
    return selected


def page_header(workflow: str) -> None:
    title, category, detail = WORKFLOWS[workflow]
    st.markdown(f'<header class="workspace-heading"><div class="eyebrow">{category}</div>'
                f'<h1>{title}</h1><p>{detail}</p></header>', unsafe_allow_html=True)


def section_header(title: str, description: str | None = None) -> None:
    detail = f'<p>{escape(description)}</p>' if description else ""
    st.markdown(f'<div class="section-heading"><h3>{escape(title)}</h3>{detail}</div>',
                unsafe_allow_html=True)


def empty_state(title: str, detail: str) -> None:
    st.markdown('''<div class="model-empty">
<svg width="140" height="140" viewBox="0 0 140 140" fill="none" aria-hidden="true">
<path d="M70 20 116 46 116 98 70 124 24 98 24 46Z" stroke="#364b6d" stroke-width="1.5"/>
<path d="m24 46 46 26 46-26M70 72v52M70 20v52M24 98l46-26 46 26" stroke="#364b6d" stroke-width="1.5"/>
<path d="M70 20 116 46 70 72 24 46Z" fill="#386bff" fill-opacity=".08" stroke="#628cff" stroke-width="1.5"/>
<path d="M70 72v52" stroke="#628cff" stroke-width="1.5"/>
<circle cx="70" cy="72" r="3.5" fill="#628cff"/>
</svg>'''
                f'<h3>{escape(title)}</h3><p>{escape(detail)}</p></div>', unsafe_allow_html=True)
