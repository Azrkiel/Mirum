"""Server-side bridge to the deterministic PicoGK nozzle job API."""
import base64
import hashlib
import json
from pathlib import Path
import re
import struct

import requests
import streamlit as st
import streamlit.components.v1 as components

FIELDS = {
    'Chamber and nozzle contour': {
        'chamber_radius_mm': 'Chamber radius (mm)',
        'chamber_length_mm': 'Chamber length (mm)',
        'convergent_length_mm': 'Convergent length (mm)',
        'throat_radius_mm': 'Throat radius (mm)',
        'divergent_length_mm': 'Divergent length (mm)',
        'exit_radius_mm': 'Exit radius (mm)',
    },
    'Cooling channels and manifolds': {
        'wall_thickness_mm': 'Wall thickness (mm)',
        'channel_count': 'Channel count',
        'channel_radius_mm': 'Channel radius (mm)',
        'channel_twist_rad_per_mm': 'Channel twist (rad/mm)',
        'manifold_radius_mm': 'Manifold radius (mm)',
        'end_margin_mm': 'End margin (mm)',
    },
    'Mesh resolution': {'voxel_size_mm': 'Voxel size (mm)'},
}
ENDPOINT = '/engineering/regen'


def _detail(response):
    try:
        detail = response.json().get('detail')
        if isinstance(detail, list):
            return '; '.join(item.get('msg', 'Invalid parameter') for item in detail)
        if isinstance(detail, str):
            return detail
    except ValueError:
        pass
    return 'The nozzle service could not complete this request.'


def _download(url, headers, limit):
    response = requests.get(url, headers=headers(), timeout=60, stream=True)
    try:
        response.raise_for_status()
        data = bytearray()
        for chunk in response.iter_content(chunk_size=1 << 20):
            if len(data) + len(chunk) > limit:
                raise ValueError('Artifact exceeds the main UI download limit. Use the standalone browser app.')
            data.extend(chunk)
        return bytes(data)
    finally:
        response.close()


def _accepted_files(backend, headers, job, specification):
    job_id = job['id']
    if not re.fullmatch('[0-9a-f]{32}', job_id):
        raise ValueError('Invalid generation ID')
    report = job.get('report') or {}
    if (report.get('passed') is not True or report.get('engineering_certified') is not False
            or report.get('geometry_source') != 'picogk_builder'):
        raise ValueError('Result does not match the required geometry acceptance evidence.')
    # Construct only known local routes; never follow an artifact URL from a report.
    files = {name: _download(f'{backend}{ENDPOINT}/jobs/{job_id}/artifacts/{name}', headers,
                            (256 << 20) if name == 'stl' else (4 << 20))
             for name in ('stl', 'report', 'spec')}
    if (hashlib.sha256(files['stl']).hexdigest() != report.get('stl_sha256')
            or hashlib.sha256(files['spec']).hexdigest() != report.get('spec_sha256')
            or json.loads(files['report']) != report
            or json.loads(files['spec']) != specification):
        raise ValueError('Downloaded artifacts do not match the accepted report and submitted parameters.')
    return files


def _preview_html(mesh):
    """Bound browser payload to 60,000 sampled triangles; downloads stay complete."""
    if len(mesh) < 84:
        raise ValueError('Invalid binary STL')
    count = struct.unpack_from('<I', mesh, 80)[0]
    if not count or len(mesh) != 84 + count * 50:
        raise ValueError('Invalid binary STL')
    stride = max(1, (count + 59999) // 60000)
    view = memoryview(mesh)
    sampled = mesh[:80] + struct.pack('<I', (count + stride - 1) // stride)
    sampled += b''.join(view[84 + i * 50:134 + i * 50] for i in range(0, count, stride))
    encoded = base64.b64encode(sampled).decode('ascii')
    viewer = (Path(__file__).parent / 'regen' / 'viewer.js').read_text(encoding='utf-8')
    return ('<!doctype html><html><head><meta charset="utf-8"><style>'
            'body{margin:0;background:#111a13;color:#dce6df;font:14px system-ui}'
            'canvas{width:100%;height:460px;display:block;touch-action:none}'
            'button{margin:8px;padding:6px 12px}#error{padding:8px}</style></head><body>'
            '<canvas id="viewer" tabindex="0" aria-label="Nozzle mesh: drag or use arrow keys to rotate"></canvas>'
            '<button id="reset-view">Reset view</button><span>Drag to rotate; scroll to zoom</span>'
            '<p id="error" role="status"></p><script>' + viewer + '\n'
            'try{const bytes=Uint8Array.from(atob("' + encoded + '"),c=>c.charCodeAt(0));'
            'makeViewer(document.getElementById("viewer")).load(bytes.buffer);}'
            'catch(e){document.getElementById("error").textContent=e.message;}'
            '</script></body></html>')


def _render_job(backend, headers):
    job = st.session_state.get('regen_job')
    if not job:
        return
    try:
        if job['status'] in ('queued', 'running'):
            response = requests.get(f'{backend}{ENDPOINT}/jobs/{job["id"]}', headers=headers(), timeout=15)
            response.raise_for_status()
            job = response.json()
            st.session_state.regen_job = job
        st.write(f"**Generation status:** {job['status']}")
        st.caption(job.get('message', ''))
        if job['status'] in ('queued', 'running'):
            st.info('Building and checking the nozzle. Status updates automatically; generation can take several minutes.')
            st.button('Refresh status', key='regen_refresh')
            return
        if job['status'] != 'accepted':
            st.error('No accepted mesh is available for this generation.')
            if job.get('report'):
                st.json(job['report'])
            return
        if not st.session_state.get('regen_files'):
            files = _accepted_files(backend, headers, job, st.session_state.regen_submitted_spec)
            # Validate preview format before exposing downloads or caching artifacts.
            preview = _preview_html(files['stl'])
            st.session_state.regen_files = files
            st.session_state.regen_preview = preview
        files = st.session_state.regen_files
        st.success('Mesh passed the geometry checks.')
        components.html(st.session_state.regen_preview, height=520)
        st.caption('Preview samples up to 60,000 triangles. The STL download contains the complete accepted mesh.')
        for column, name, label, filename in zip(st.columns(3), ('stl', 'report', 'spec'),
                ('Download STL', 'Download geometry report', 'Download parameters'),
                ('nozzle.stl', 'report.json', 'spec.canonical.json')):
            column.download_button(label, files[name], file_name=filename,
                                   mime='model/stl' if name == 'stl' else 'application/json', key=f'regen_download_{name}')
        with st.expander('Accepted parameters and geometry evidence'):
            st.json(json.loads(files['spec']))
            st.json(job['report'])
    except (requests.RequestException, ValueError, KeyError):
        st.session_state.pop('regen_files', None)
        st.session_state.pop('regen_preview', None)
        st.error('Could not load a result that matches the accepted report and submitted parameters. '
                 'Retry status or generate again if the result has expired.')


def render_regen(backend, headers):
    st.title('Regenerative cooling rocket nozzle')
    st.write('Deterministic PicoGK geometry: chamber, convergent section, throat, divergent nozzle, '
             'cooling channels, and inlet/outlet manifolds. No model API is used.')
    st.warning('Geometry checks only. No gas dynamics, thermal, structural, or manufacturing analysis was done. '
               'Do not manufacture or fire hardware based on this output.')
    try:
        response = requests.get(f'{backend}{ENDPOINT}/config', headers=headers(), timeout=15)
        response.raise_for_status()
        config = response.json()
    except (requests.RequestException, ValueError):
        st.error('The deterministic nozzle service is unavailable. Check that the backend is running.')
        return
    if not config['ready']:
        st.warning('PicoGK runtime is not installed on the backend. Follow docs/implementation/regen-browser.md '
                   'to install it on a Linux x86-64 host.')
    preset = st.selectbox('Nozzle preset', list(config['examples']), format_func=str.capitalize, key='regen_preset')
    specification = dict(config['examples'][preset])
    with st.form('regen_parameters'):
        for group, fields in FIELDS.items():
            st.markdown(f'**{group}**')
            columns = st.columns(3)
            for index, (name, label) in enumerate(fields.items()):
                schema = config['schema']['properties'][name]
                integer = schema['type'] == 'integer'
                cast = int if integer else float
                minimum = schema.get('minimum', schema.get('exclusiveMinimum', 0) + 0.000001)
                specification[name] = columns[index % 3].number_input(label,
                    min_value=cast(minimum), max_value=cast(schema['maximum']),
                    value=cast(specification[name]), step=1 if integer else 0.001,
                    format='%d' if integer else '%.3f', key=f'regen_{preset}_{name}')
        st.caption('Dimensions are in millimetres. Smaller voxels take more time and memory. '
                   'The backend checks wall, rib, channel, slope and mesh-size constraints before building.')
        submitted = st.form_submit_button('Generate nozzle mesh', disabled=not config['ready'], type='primary')
    if submitted:
        try:
            response = requests.post(f'{backend}{ENDPOINT}/jobs', json=specification, headers=headers(), timeout=20)
            response.raise_for_status()
            st.session_state.regen_job = response.json()
            st.session_state.regen_submitted_spec = specification
            st.session_state.pop('regen_files', None)
            st.session_state.pop('regen_preview', None)
        except requests.HTTPError:
            st.error(_detail(response))
        except (requests.RequestException, ValueError):
            st.error('Could not start nozzle generation. Check the backend and try again.')
    running = st.session_state.get('regen_job', {}).get('status') in ('queued', 'running')
    st.fragment(run_every='2s' if running else None)(_render_job)(backend, headers)
