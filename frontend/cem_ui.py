"""Mission, feasibility, process archive and explicitly screened build bundles."""
import json
import requests
import streamlit as st

try:
    from workspace import section_header, empty_state
except ModuleNotFoundError:
    from frontend.workspace import section_header, empty_state


def render_cem(backend, headers):
    st.caption('Define the mission, compare processes, and inspect the best screened designs.')
    with st.expander('Model scope'):
        st.caption('Screening models with engineering uncertainty priors. Current layouts: conventional, T-tail and V-tail. '
                   'Bundles contain analysis geometry and templates; manufactured parts and novel layouts are pending qualification.')
    with st.expander('Import mission from text'):
        text = st.text_area('Mission', placeholder='Maximize distance from a 10 m drop; 1200 m reference target; compare all processes.')
        if st.button('Translate to requirements') and text.strip():
            try:
                r = requests.post(f'{backend}/cem/glider/requirements', json={'prompt': text}, headers=headers(), timeout=40)
                r.raise_for_status()
                value = r.json()
                if value['status'] == 'needs_clarification':
                    for question in value['questions']:
                        st.warning(question)
                else:
                    st.session_state.cem_extracted = json.dumps(value['requirements'], indent=2)
            except (requests.RequestException, ValueError):
                st.error('Text translation is unavailable. The mission form below works without a model provider.')
        if 'cem_extracted' in st.session_state:
            st.code(st.session_state.cem_extracted, language='json')
            st.caption('Review these translated requirements. Paste into the advanced mission input below to use them.')
    with st.form('cem_mission'):
        left, middle, right = st.columns(3, gap='medium')
        with left:
            with st.container(border=True):
                section_header('Mission & launch')
                objective = st.selectbox('Maximize', ['distance_m', 'duration_s'],
                                        format_func=lambda v: 'Distance (m)' if v == 'distance_m' else 'Duration (s)')
                reference = st.number_input('Reference stretch goal (m or s)', 0., 1000000., 1200.)
                height = st.number_input('Release height (m)', 0., 100., 10.)
                method = st.selectbox('Launch method', ['drop', 'hand_toss', 'discus', 'catapult', 'hi_start'],
                                      format_func=lambda v: v.replace('_', ' ').capitalize())
                explicit_speed = st.checkbox('Specify release speed')
                speed = st.number_input('Release speed (m/s)', 0., 60., 8.)
        with middle:
            with st.container(border=True):
                section_header('Airframe & payload')
                span = st.number_input('Maximum span (m)', .6, 1.4, 1.4)
                length = st.number_input('Maximum length (m)', .45, .95, .95)
                mass = st.number_input('Maximum mass (g)', 1., 500., 500.)
                payload = st.number_input('Payload mass (g)', 0., 300., 60.)
                diameter = st.number_input('Payload diameter (mm)', 1., 100., 45.)
                payload_length = st.number_input('Payload length (mm)', 1., 300., 60.)
        with right:
            with st.container(border=True):
                section_header('Design search')
                process = st.selectbox('Process', ['auto', 'sheet_foam', 'balsa', 'fdm_lwpla', 'fdm_pla', 'hybrid'],
                    format_func=lambda v: {'auto':'Compare all', 'sheet_foam':'Sheet foam', 'balsa':'Balsa',
                                          'fdm_lwpla':'FDM LW-PLA', 'fdm_pla':'FDM PLA', 'hybrid':'Hybrid'}[v])
                wing_shape = st.selectbox('Wing planform search', ['baseline', 'multi_station'],
                    format_func=lambda v: {'baseline': 'Existing wing templates',
                                           'multi_station': 'Five-station chord shapes (screening only)'}[v],
                    help='Varies three interior chords by up to 15%. Existing tail families; no independently verified novelty or manufacturing claim.')
                families = st.multiselect('Layout families', ['conventional', 't_tail', 'v_tail'],
                                          default=['conventional', 't_tail', 'v_tail'],
                                          format_func=lambda v: {'conventional':'Conventional', 't_tail':'T-tail', 'v_tail':'V-tail'}[v])
                budget = st.number_input('Evaluations per process', 8, 120, 40)
                seed = st.number_input('Seed', 0, 2147483647, 42)
        with st.expander('Environment & performance limits'):
            air, weather, limits = st.columns(3, gap='medium')
            environment = air.selectbox('Environment', ['still_air', 'wind', 'uniform_uplift'],
                                        format_func=lambda v: v.replace('_', ' ').capitalize())
            wind = air.number_input('Along-path wind (m/s; positive = tailwind)', -15., 15., 0.)
            uplift = air.number_input('Mean uplift (m/s)', 0., 5., 0.)
            altitude = weather.number_input('Altitude above sea level (m)', 0., 5000., 0.)
            temperature = weather.number_input('Ambient temperature (°C)', -30., 45., 15.)
            hard = limits.checkbox('Also enforce a hard minimum at P10',
                                   help='The lower 10th percentile of the model uncertainty band must meet this value.')
            minimum = limits.number_input('Hard minimum (m or s)', .001, 1000000., 100.)
        with st.expander('Advanced typed mission JSON and pinned calibration'):
            advanced = st.text_area('Mission JSON (overrides the form when filled)', height=160)
            cal_version = st.text_input('Calibration content hash (empty = initial engineering priors)')
        check_col, search_col = st.columns(2)
        screen = check_col.form_submit_button('Check feasibility', use_container_width=True)
        search = search_col.form_submit_button('Discover best designs', type='primary',
            disabled=st.session_state.get('cem_status') == 'running', use_container_width=True)
    if screen or search:
        try:
            req = json.loads(advanced) if advanced.strip() else dict(objective=objective, mode='maximize',
                reference_target=reference or None, minimum_performance=minimum if hard else None,
                launch=dict(height_m=height, method=method, launch_speed_m_s=speed if explicit_speed else None),
                environment=dict(kind=environment, wind_m_s=wind if environment == 'wind' else 0.,
                    uplift_m_s=uplift if environment == 'uniform_uplift' else 0., altitude_m=altitude, temperature_c=temperature),
                constraints=dict(max_span_m=span, max_length_m=length, max_mass_kg=mass/1000,
                    payload=dict(mass_kg=payload/1000, diameter_m=diameter/1000, length_m=payload_length/1000) if payload else None),
                process=process, discovery=dict(families=families, wing_shape=wing_shape, budget_evals=budget, seed=seed, top_k=3))
            body = dict(requirements=req, calibration_version=cal_version or None)
            r = requests.post(f'{backend}/cem/glider/feasibility', json=body, headers=headers(), timeout=15)
            r.raise_for_status()
            st.session_state.cem_feasibility = r.json()
            if search:
                r = requests.post(f'{backend}/cem/glider/discover', json=body, headers=headers(), timeout=15)
                r.raise_for_status()
                st.session_state.update(cem_job=r.json()['job_id'], cem_status='running', cem_kind='discovery')
                st.rerun()
        except (requests.RequestException, ValueError) as exc:
            st.error(f'Could not submit mission: {exc}')
    if 'cem_feasibility' in st.session_state:
        section_header('Feasibility')
        f = st.session_state.cem_feasibility
        st.write(f"Reference goal: **{f['verdict']} within the sampled model envelope**. Search continues to maximize performance.")
        if f['required_lift_to_drag'] is not None and f['optimistic_lift_to_drag'] is not None:
            st.write(f"Required L/D: {f['required_lift_to_drag']:.1f}; optimistic envelope: {f['optimistic_lift_to_drag']:.1f}.")
        if f.get('bound_state') == 'rebuild_required_for_calibration':
            st.info('This calibration needs a regenerated feasibility envelope. Continue with physics sizing; no infeasibility claim is made.')
        if f.get('bound_state') == 'rebuild_required_for_shape':
            st.info('The existing feasibility bound does not cover this wing shape search. Candidate screening continues without an infeasibility claim.')
        st.dataframe(f['levers'], hide_index=True, use_container_width=True)
        with st.expander('Feasibility evidence and assumptions'):
            st.json(f)
    _results(backend, headers)


@st.fragment(run_every=3)
def _results(backend, headers):
    key = st.session_state.get('cem_job')
    if not key:
        if 'cem_feasibility' not in st.session_state:
            empty_state('No engineering results', 'Check feasibility or discover designs for this mission.')
        return
    section_header('Design results')
    try:
        response = requests.get(f'{backend}/cem/glider/jobs/{key}', headers=headers(), timeout=10)
        response.raise_for_status()
        job = response.json()
        st.session_state.cem_status = job['status']
        if job['status'] == 'running':
            st.info(f"Running bounded CEM {st.session_state.get('cem_kind', 'job')}")
            with st.expander('Search progress', expanded=True):
                st.json(job.get('progress', {}))
            return
        if job['status'] == 'failed':
            st.error(job['detail'])
            return
        result = job['result']
        if result.get('kind') == 'shape_comparison':
            st.write(f"Paired shape comparison: **{result['status']}**.")
            st.info('No verified performance gain is claimed. Hardware is fixed; mass and CG can change with shape. '
                    'Independent diagnostics do not close whole-aircraft viscous performance.')
            rows = [dict(arm=label, admitted=result[label]['result']['valid'],
                         objective=result[label]['result'].get('objective_value'),
                         failures=result[label]['result']['fail_reasons']) for label in ('baseline', 'shaped')]
            st.dataframe(rows, hide_index=True, use_container_width=True)
            if result['objective_delta'] is not None:
                st.write(f"Paired screening change (shaped − unity control): **{result['objective_delta']:+.3f}**.")
            st.write(f"Independent XFOIL/AVL diagnostics: **{result['independent_recheck']['state']}**.")
            st.download_button('Download paired comparison evidence', json.dumps(result, indent=2),
                file_name=f"mirum-shape-comparison-{result['comparison_sha256'][:12]}.json", mime='application/json')
            with st.expander('Comparison evidence'):
                st.json(result)
            if st.session_state.get('cem_discovery_job') and st.button('Return to discovery archive'):
                st.session_state.update(cem_job=st.session_state.cem_discovery_job, cem_kind='discovery')
                st.rerun()
            return
        if 'per_process' not in result:
            st.write(f"Closure result: **{result['status']}**. Manufacturing acceptance: **not qualified**.")
            if result['status'] == 'screened':
                r = requests.get(f'{backend}/cem/glider/jobs/{key}/bundle', headers=headers(), timeout=20)
                r.raise_for_status()
                st.download_button('Download screening geometry, templates and evidence', r.content,
                    file_name=f"mirum-cem-{result['output_id']}.zip", mime='application/zip')
            with st.expander('Closure evidence'):
                st.json(result)
            return
        rows = []
        elites = []
        for process, data in result['per_process'].items():
            best = data['best']
            rows.append(dict(process=process, best=best['result']['objective_value'] if best else None,
                             occupied_cells=len(data['archive']), state='screened' if best else 'no admitted design'))
            elites.extend(data['archive'])
        st.dataframe(rows, hide_index=True, use_container_width=True,
                     column_config={'process': 'Process', 'best': 'Best performance',
                                    'occupied_cells': 'Archive cells', 'state': 'State'})
        # Categorical family/AR/wing-loading cells form a compact archive map.
        if elites:
            import pandas as pd
            cells = [dict(family=e['result']['tail_kind'], aspect_ratio=e['result']['aspect_ratio'],
                          wing_loading=e['result']['wing_loading_g_dm2'], performance=e['result']['objective_value'],
                          process=e['process'], elite_id=e['elite_id']) for e in elites]
            import altair as alt
            chart = alt.Chart(pd.DataFrame(cells)).mark_circle(size=120).encode(
                x=alt.X('aspect_ratio:Q', title='Aspect ratio'),
                y=alt.Y('wing_loading:Q', title='Wing loading (g/dm²)'),
                color=alt.Color('performance:Q', title='Performance',
                                scale=alt.Scale(range=['#344b82', '#6893ff'])),
                shape=alt.Shape('family:N', title='Layout'),
                tooltip=['family', 'process', 'elite_id', 'performance']).interactive()
            st.altair_chart(chart, use_container_width=True)
            chosen = st.selectbox('Inspect an archive elite', elites,
                format_func=lambda e: f"{e['process']} / {e['result']['tail_kind']} / {e['result']['objective_value']:.1f} / {e['elite_id']}")
            with st.expander('Elite physics and constraints'):
                st.json(chosen)
            if 'chord_factors' in chosen['parameters']['wing']:
                independent = st.checkbox('Run independent XFOIL and AVL rechecks',
                    help='Requires server-installed executables. Missing or failed checks remain explicit; no verified gain is claimed.')
                if st.button('Compare shape with fixed hardware'):
                    r = requests.post(f'{backend}/cem/glider/compare-shape',
                        json=dict(discovery_job_id=key, elite_id=chosen['elite_id'], independent_recheck=independent),
                        headers=headers(), timeout=15)
                    r.raise_for_status()
                    st.session_state.update(cem_discovery_job=key, cem_job=r.json()['job_id'],
                                            cem_status='running', cem_kind='comparison')
                    st.rerun()
            if st.button('Build analysis geometry and check mass closure'):
                r = requests.post(f'{backend}/cem/glider/design', json=dict(discovery_job_id=key, elite_id=chosen['elite_id']),
                                  headers=headers(), timeout=15)
                r.raise_for_status()
                st.session_state.update(cem_job=r.json()['job_id'], cem_status='running', cem_kind='design')
                st.rerun()
        else:
            st.warning('No elite met the declared physics/model/process constraints. Review the rejection evidence.')
        with st.expander('Archive evidence and model limits'):
            st.json(result)
    except (requests.RequestException, ValueError, KeyError) as exc:
        st.error(f'CEM result unavailable: {exc}')
