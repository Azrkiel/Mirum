"""Designer controls and polling UI for passive-glider distance searches."""
import io
import json
import zipfile

import requests
import streamlit as st

try:
    from workspace import section_header, empty_state
except ModuleNotFoundError:
    from frontend.workspace import section_header, empty_state

# Same metadata source as server validation; no duplicated range constants.
def numeric_ranges(backend, headers):
    r = requests.get(f'{backend}/gliders/metadata', headers=headers(), timeout=15)
    r.raise_for_status()
    return {family: {m['key']: tuple(m['range']) for m in fields.values() if m['numeric']}
            for family, fields in r.json()['controls'].items()}


def render_glider(backend, headers):
    st.caption('Find a glider with the highest trimmed lift-to-drag ratio, or adjust one design directly.')
    if st.session_state.pop('explorer_switch', False):
        st.session_state.glider_mode = 'Explore one design'
    mode = st.radio('Glider workflow', ['Search designs', 'Explore one design'], key='glider_mode', horizontal=True)
    with st.expander('Model scope'):
        st.caption('Predicted distance = launch height × L/D, assuming still air and steady trimmed glide '
                   'at standard sea-level conditions. Screening estimates; strength, gusts, wing flex '
                   'and launch/landing transients are not evaluated.')
    if mode == 'Explore one design':
        from glider_explorer import render_explorer
        render_explorer(backend, headers)
        return
    try:
        supported_ranges = numeric_ranges(backend, headers)
    except (requests.RequestException, ValueError, KeyError):
        st.error('Could not load supported design ranges from the backend. Retry when the API is available.')
        return
    with st.form('glider_requirements'):
        a, b, c = st.columns(3, gap='medium')
        with a:
            with st.container(border=True):
                section_header('Launch & payload')
                height = st.number_input('Launch height (m)', 0.1, 100.0, 10.0)
                mass = st.number_input('Payload mass (g)', 0.0, 300.0, 60.0)
                diameter = st.number_input('Payload diameter (mm)', 1.0, 100.0, 45.0)
                length = st.number_input('Payload length (mm)', 1.0, 300.0, 60.0)
        with b:
            with st.container(border=True):
                section_header('Size & mass limits')
                span = st.number_input('Maximum wingspan (m)', 0.6, 1.4, 1.4)
                overall = st.number_input('Maximum overall length (m)', 0.45, 0.95, 0.95)
                total = st.number_input('Maximum total mass (g)', 1.0, 500.0, 500.0)
                speed = st.number_input('Maximum flight speed (m/s)', 1.0, 15.0, 9.0)
        with c:
            with st.container(border=True):
                section_header('Flight margins')
                sm_min = st.number_input('Minimum static margin (% MAC)', 2.0, 19.0, 5.0)
                sm_target = st.number_input('Target static margin (% MAC)', 2.0, 20.0, 12.0)
                stall = st.number_input('Maximum trim CL / CLmax (%)', 50.0, 90.0, 85.0,
                                       help='85% leaves a 15% margin below the estimated peak lift coefficient.')
        section_header('Configuration')
        wing_col, body_col, tail_col = st.columns(3, gap='medium')
        wings = wing_col.multiselect('Wing styles', ['tapered', 'elliptical', 'polyhedral'],
                                    default=['tapered', 'elliptical', 'polyhedral'])
        bodies = body_col.multiselect('Fuselage styles', ['pod_boom', 'torpedo', 'slab'],
                                     default=['pod_boom', 'torpedo', 'slab'],
                                     format_func=lambda v: v.replace('_', ' ').capitalize())
        tails = tail_col.multiselect('Tail configurations', ['conventional', 'T', 'V'],
                                     default=['conventional', 'T', 'V'])
        with st.expander('Materials and mass model'):
            density_col, structure_col = st.columns(2)
            wing_density = density_col.number_input('Wing areal density (kg/m²)', 0.05, 3.0, 0.30)
            tail_density = density_col.number_input('Tail areal density (kg/m²)', 0.03, 3.0, 0.15)
            body_density = density_col.number_input('Fuselage shell areal density (kg/m²)', 0.05, 5.0, 0.50)
            hardware = structure_col.number_input('Hardware mass (g)', 0.0, 200.0, 10.0)
            wall = structure_col.number_input('Wall thickness (mm)', 0.1, 20.0, 3.0)
            wing_cg = structure_col.number_input('Wing mass CG (fraction of chord)', 0.1, 0.8, 0.42)
        ranges = {}
        with st.expander('Allowed dimensions and angles'):
            st.caption('Set the allowed interval for each parameter. Equal endpoints lock it. '
                       'Lengths are metres and angles are degrees. Maximum size limits above also apply.')
            for family, fields in supported_ranges.items():
                st.markdown(f'**{family.title()}**')
                ranges[family] = {}
                for name, (lo, hi) in fields.items():
                    label = name.replace('_m', ' (m)').replace('_deg', ' (deg)').replace('_', ' ').capitalize()
                    ranges[family][name] = list(st.slider(label, float(lo), float(hi),
                                                         (float(lo), float(hi)), step=float(hi - lo) / 100,
                                                         format='%.4f', key=f'range-{family}-{name}'))
            ranges['wing']['naca_camber_pct'] = st.multiselect('NACA camber (%)', [0, 1, 2, 3, 4], default=[0, 1, 2, 3, 4])
            ranges['wing']['naca_camber_pos'] = st.multiselect('NACA camber location (tenths of chord)', [3, 4], default=[3, 4])
            ranges['wing']['naca_thickness_pct'] = st.multiselect('NACA thickness (%)', [6, 7, 8, 9, 10, 12], default=[6, 7, 8, 9, 10, 12])
            ranges['tail']['airfoil'] = st.multiselect('Tail airfoil', ['0006', '0008'], default=['0006', '0008'])
        with st.expander('Search settings'):
            budget_col, seed_col, top_col = st.columns(3)
            budget = budget_col.number_input('Evaluation budget', 8, 120, 40)
            seed = seed_col.number_input('Random seed', 0, 2147483647, 42)
            top = top_col.number_input('Leading candidates', 1, 10, 5)
        running = st.session_state.get('glider_status') == 'running'
        submitted = st.form_submit_button('Search glider designs', type='primary', disabled=running,
                                         use_container_width=True)
    if submitted:
        try:
            if (not wings or not bodies or not tails or not ranges['tail']['airfoil']
                    or any(not ranges['wing'][k] for k in ('naca_camber_pct', 'naca_camber_pos', 'naca_thickness_pct'))):
                raise ValueError('Choose at least one configuration and airfoil option for every part.')
            ranges.setdefault('wing', {})['style'] = wings
            ranges.setdefault('fuselage', {})['style'] = bodies
            ranges.setdefault('tail', {})['kind'] = tails
            body = dict(launch_height_m=height, payload_mass_kg=mass / 1000,
                        payload_diameter_m=diameter / 1000, payload_length_m=length / 1000,
                        max_wingspan_m=span, max_length_m=overall, max_total_mass_kg=total / 1000,
                        max_flight_speed_m_s=speed, static_margin_min=sm_min / 100,
                        static_margin_target=sm_target / 100, stall_margin=stall / 100,
                        evaluations=budget, seed=seed, top_k=top, ranges=ranges,
                        materials=dict(wing_areal_density_kg_m2=wing_density,
                                       tail_areal_density_kg_m2=tail_density,
                                       fuselage_shell_areal_density_kg_m2=body_density,
                                       fixed_hardware_mass_kg=hardware / 1000,
                                       wall_thickness_m=wall / 1000, wing_cg_chord_fraction=wing_cg))
            response = requests.post(f'{backend}/gliders/search', json=body, headers=headers(), timeout=30)
            if response.status_code != 202:
                raise ValueError(response.json().get('detail', 'Search could not start'))
            st.session_state.glider_job = response.json()['job_id']
            st.session_state.glider_status = 'running'
            st.session_state.glider_downloads = {}
            st.rerun()
        except (ValueError, requests.RequestException) as exc:
            st.error(f'Could not start search: {exc}')
    _results(backend, headers)


@st.fragment(run_every=2)
def _results(backend, headers):
    key = st.session_state.get('glider_job')
    if not key:
        empty_state('No search results', 'Set the requirements and search for candidate designs.')
        return
    section_header('Search results')
    try:
        response = requests.get(f'{backend}/gliders/search/{key}', headers=headers(), timeout=10)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError):
        st.warning('Could not retrieve search status. Refresh to retry; the search may still be running.')
        if st.button('Clear expired search'):
            st.session_state.pop('glider_job', None)
            st.session_state.glider_status = 'idle'
            st.rerun()
        return
    previous = st.session_state.get('glider_status')
    st.session_state.glider_status = data['status']
    if previous == 'running' and data['status'] != 'running':
        st.rerun()
    if data['status'] == 'running':
        p = data.get('progress', {})
        st.progress(p.get('evaluated', 0) / max(1, p.get('budget', 1)))
        st.caption(f"{p.get('evaluated', 0)} evaluated · {p.get('feasible', 0)} feasible. "
                   'Promising candidates are refined before export.')
        return
    if data['status'] == 'failed':
        st.error(data['detail'])
        return
    result = data['result']
    st.success(f"{result['feasible']} feasible designs / {result['evaluated']} evaluated")
    st.caption(result['assumptions'])
    with st.expander('Rejected candidates'):
        st.json(result['rejections'])
    st.download_button('Download search report', json.dumps(result, indent=2),
                       'mirum-glider-search.json', 'application/json')
    if not result['candidates']:
        st.warning('No feasible design was found in this budget. Inspect rejection reasons and revise '
                   'the design space or increase the budget. This does not prove the requirements are impossible.')
        return
    columns = ['rank', 'id', 'L_over_D', 'glide_distance_m', 'mass_kg', 'glide_speed_m_s',
               'static_margin', 'CL_trim', 'CLmax_est']
    st.dataframe([{k: r[k] for k in columns} for r in result['candidates']], hide_index=True,
                 use_container_width=True, column_config={
                     'rank': 'Rank', 'id': 'Design', 'L_over_D': 'L/D',
                     'glide_distance_m': 'Distance (m)', 'mass_kg': 'Mass (kg)',
                     'glide_speed_m_s': 'Speed (m/s)', 'static_margin': 'Static margin',
                     'CL_trim': 'Trim CL', 'CLmax_est': 'Estimated CLmax',
                 })
    for candidate in result['candidates']:
        cid = candidate['id']
        with st.expander(f"#{candidate['rank']} · {cid} · predicted distance {candidate['glide_distance_m']:.1f} m"):
            st.write(f"Static margin: {candidate['static_margin']:.1%} MAC · "
                     f"Stall margin: {1 - candidate['CL_trim'] / candidate['CLmax_est']:.1%} · "
                     f"Tail incidence: {candidate['tail_incidence_deg']:.2f}°")
            explore_col, files_col = st.columns(2)
            if explore_col.button('Explore this candidate', key=f'explore-{cid}', use_container_width=True):
                try:
                    r = requests.get(f'{backend}/gliders/search/{key}/design/{cid}', headers=headers(), timeout=15)
                    r.raise_for_status()
                    st.session_state.explorer_bootstrap = r.json()
                    st.session_state.pop('explorer_epoch', None)
                    st.session_state.pop('explorer_reply', None)
                    st.session_state.pop('explorer_evaluation', None)
                    st.session_state.explorer_switch = True
                    st.rerun()
                except (requests.RequestException, ValueError):
                    st.error('Could not load candidate. Please retry.')
            if files_col.button('Load geometry and drawing', key=f'load-{cid}', use_container_width=True):
                try:
                    r = requests.get(f'{backend}/gliders/search/{key}/geometry/{cid}', headers=headers(), timeout=30)
                    r.raise_for_status()
                    st.session_state.glider_downloads[cid] = r.content
                except requests.RequestException:
                    st.error('Geometry download failed. Please retry.')
            payload = st.session_state.get('glider_downloads', {}).get(cid)
            if payload:
                with zipfile.ZipFile(io.BytesIO(payload)) as z:
                    st.image(z.read('three_view.png'))
                st.download_button('Download geometry, templates and parameters', payload,
                                   f'{cid}.zip', 'application/zip', key=f'zip-{cid}')
            st.json(candidate['parameters'], expanded=False)
