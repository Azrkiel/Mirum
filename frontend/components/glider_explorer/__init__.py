"""Persistent browser viewer; the Python bridge keeps access tokens out of the iframe."""
from pathlib import Path
import streamlit.components.v1 as components

explorer = components.declare_component('glider_explorer', path=str(Path(__file__).parent / 'dist'))
