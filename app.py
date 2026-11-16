from pathlib import Path
import html
import re
import sqlite3

import altair as alt
import folium
import geopandas as gpd
import pandas as pd
import pyogrio
import streamlit as st
from folium.plugins import Fullscreen
from shapely.geometry import Point
from streamlit_folium import st_folium

st.set_page_config(page_title='Visor de cobertura vegetal por microcuenca', page_icon='🌿', layout='wide', initial_sidebar_state='expanded')


st.markdown('''
<style>
.leaflet-tooltip .foliumtooltip table {
    border-collapse: collapse !important;
    width: auto !important;
}
.leaflet-tooltip .foliumtooltip th {
    text-align: left !important;
    padding: 2px 8px 2px 0 !important;
    white-space: nowrap !important;
}
.leaflet-tooltip .foliumtooltip td {
    text-align: left !important;
    padding: 2px 0 !important;
    white-space: normal !important;
}
</style>
''', unsafe_allow_html=True)

@st.cache_resource
def rutas():
    import hashlib
    import requests

    datos = Path(__file__).resolve().parent / "datos"
    datos.mkdir(parents=True, exist_ok=True)

    archivo = datos / "TB_COBERTURA_GEOM.gpkg"

    url = (
        "https://media.githubusercontent.com/media/"
        "RodriCueva/-visor-cobertura-vegetal/"
        "372775d/datos/TB_COBERTURA_GEOM.gpkg"
    )

    sha_esperado = (
        "d35b7022c68c99f7848af3d6279f82e8"
        "cfcfc9ab80a01351b6b9a666c17b70e2"
    )

    tamano_esperado = 501075968

    def verificar(ruta):
        if not ruta.exists():
            return False

        if ruta.stat().st_size != tamano_esperado:
            return False

        h = hashlib.sha256()

        with open(ruta, "rb") as f:
            for bloque in iter(
                lambda: f.read(8 * 1024 * 1024), b""
            ):
                h.update(bloque)

        return h.hexdigest() == sha_esperado

    if not verificar(archivo):

        temporal = datos / "TB_COBERTURA_GEOM.descarga"

        try:
            with st.spinner(
                "Preparando la base geoespacial..."
            ):
                with requests.get(
                    url,
                    stream=True,
                    timeout=(30, 120)
                ) as respuesta:

                    respuesta.raise_for_status()

                    h = hashlib.sha256()
                    total = 0

                    with open(temporal, "wb") as f:
                        for bloque in respuesta.iter_content(
                            chunk_size=8 * 1024 * 1024
                        ):
                            if bloque:
                                f.write(bloque)
                                h.update(bloque)
                                total += len(bloque)

                    if total != tamano_esperado:
                        raise RuntimeError(
                            "Tamaño de descarga incorrecto."
                        )

                    if h.hexdigest() != sha_esperado:
                        raise RuntimeError(
                            "SHA256 de descarga incorrecto."
                        )

            temporal.replace(archivo)

        finally:
            if temporal.exists():
                temporal.unlink()

    for nombre in [
        "BD_COBERTURA_VEGETAL.sqlite",
        "TB_MICROCUENCAS_VISOR.gpkg",
        "TB_COBERTURA_GEOM.gpkg"
    ]:
        if not (datos / nombre).exists():
            raise FileNotFoundError(
                f"Archivo no encontrado: {datos / nombre}"
            )

    return datos
try:
    RESULTADOS = rutas()
except Exception as exc:
    st.error(str(exc))
    st.stop()

BD = RESULTADOS / 'BD_COBERTURA_VEGETAL.sqlite'
GEOM = RESULTADOS / 'TB_COBERTURA_GEOM.gpkg'
MICRO = RESULTADOS / 'TB_MICROCUENCAS_VISOR.gpkg'
CAPA = 'TB_COBERTURA_GEOM'
CAPA_MICRO = 'TB_MICROCUENCAS_VISOR'
for ruta in (BD, GEOM, MICRO):
    if not ruta.exists():
        st.error(f'Archivo no encontrado: {ruta}')
        st.stop()

UH_NOMBRES = {1:'Huaura', 2:'Mantaro', 3:'Huaral', 4:'Chillón', 5:'Rímac', 6:'Lurín', 7:'Mala', 8:'Cañete', 9:'Alto Huallaga', 10:'Perené', 11:'Pachitea'}
TILES = {
    'Relieve topográfico (Esri)': ('https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}', 'Tiles © Esri'),
    'CartoDB claro': ('CartoDB positron', 'CartoDB'),
    'OpenStreetMap': ('OpenStreetMap', 'OpenStreetMap'),
    'Sin mapa base': (None, None),
}

@st.cache_data(show_spinner='Cargando inventario de microcuencas...')
def inventario(path, mtime):
    with sqlite3.connect(path) as con:
        df = pd.read_sql_query('SELECT ID_MICRO, ID_UH, AREA_MICRO_KM2 FROM TB_MICROCUENCAS_REF ORDER BY ID_UH, ID_MICRO', con)
    df['ID_MICRO'] = df['ID_MICRO'].astype('int64')
    df['ID_UH'] = df['ID_UH'].astype('int64')
    return df

@st.cache_data(show_spinner=False, max_entries=12)
def microcuencas_uh(path, mtime, id_uh):
    g = pyogrio.read_dataframe(path, layer=CAPA_MICRO, where=f'ID_UH = {int(id_uh)}', columns=['ID_MICRO'])
    if g.crs is None:
        raise ValueError('La capa de microcuencas no tiene CRS.')
    return g[['ID_MICRO', 'geometry']].to_crs(4326)

@st.cache_data(show_spinner=False, max_entries=100)
def microcuenca_detalle(path, mtime, mid):
    g = pyogrio.read_dataframe(path, layer=CAPA_MICRO, where=f'ID_MICRO = {int(mid)}')
    if not g.empty and g.crs is None:
        raise ValueError('La microcuenca no tiene CRS.')
    return g

@st.cache_data(show_spinner=False, max_entries=100)
def resumen_micro(path, mtime, mid):
    with sqlite3.connect(path) as con:
        return pd.read_sql_query('''SELECT ID_COB, CLASE, GRUPO, TIPO, HEX_COLOR, AREA_M2, AREA_KM2, PORCENTAJE
                                    FROM TB_COBERTURA_VEGETAL WHERE ID_MICRO = ? ORDER BY AREA_KM2 DESC''', con, params=(int(mid),))

@st.cache_data(show_spinner=False, max_entries=100)
def control_micro(path, mtime, mid):
    with sqlite3.connect(path) as con:
        return pd.read_sql_query('SELECT * FROM TB_CONTROL_COBERTURA WHERE ID_MICRO = ?', con, params=(int(mid),))

@st.cache_data(show_spinner='Cargando coberturas de la microcuenca...', max_entries=25)
def cobertura_micro(path, mtime, mid):
    g = pyogrio.read_dataframe(path, layer=CAPA, where=f'ID_MICRO = {int(mid)}')
    if not g.empty and g.crs is None:
        raise ValueError('La cobertura vegetal no tiene CRS.')
    return g

@st.cache_data(show_spinner=False)
def colores_globales(path, mtime):
    with sqlite3.connect(path) as con:
        df = pd.read_sql_query('SELECT ID_COB, HEX_COLOR FROM TB_COBERTURA_VEGETAL GROUP BY ID_COB, HEX_COLOR', con)
    def valido(c):
        return isinstance(c, str) and re.fullmatch(r'#[0-9A-Fa-f]{6}', c.strip()) is not None
    inconsistencias = df.groupby('ID_COB')['HEX_COLOR'].nunique(dropna=False)
    if (inconsistencias > 1).any():
        raise ValueError(f'Colores inconsistentes para ID_COB: {inconsistencias[inconsistencias > 1].index.tolist()}')
    if not df['HEX_COLOR'].map(valido).all():
        raise ValueError('Se encontraron valores HEX_COLOR inválidos; revisar la tabla original.')
    return {int(r.ID_COB): str(r.HEX_COLOR).strip() for r in df.itertuples()}

def crear_mapa(base):
    tiles, attr = TILES[base]
    m = folium.Map(location=[-11.3, -76.5], zoom_start=8, tiles=tiles, attr=attr,
                   control_scale=True, prefer_canvas=True)
    Fullscreen(position='topright').add_to(m)
    return m

st.markdown('''<style>
.block-container{padding-top:1.25rem;padding-bottom:2rem;max-width:1550px}
[data-testid="stMetric"]{border:1px solid rgba(140,150,150,.22);border-radius:12px;padding:12px 15px}
[data-testid="stMetricLabel"]{font-size:.85rem}
[data-testid="stMetricValue"]{font-size:1.65rem}
.legend-row{display:flex;align-items:center;gap:8px;margin:5px 0;font-size:.87rem}
.legend-swatch{width:13px;height:13px;flex:none;border:1px solid #777;border-radius:2px}
.cob-table{width:100%;border-collapse:collapse;table-layout:fixed;font-size:.89rem}
.cob-table th,.cob-table td{border:1px solid rgba(140,150,150,.3);padding:12px;vertical-align:top;text-align:left;overflow-wrap:anywhere;white-space:normal;line-height:1.5}
.cob-table th{background:rgba(140,150,150,.12)}
.cob-table th:nth-child(1){width:10%}.cob-table th:nth-child(2){width:28%}.cob-table th:nth-child(3){width:18%}
.cob-table th:nth-child(4){width:18%}.cob-table th:nth-child(5){width:13%}.cob-table th:nth-child(6){width:13%}
.cob-num{text-align:right!important;white-space:nowrap!important;font-variant-numeric:tabular-nums}
.cob-dot{display:inline-block;width:11px;height:11px;border-radius:2px;margin-right:7px}
</style>''', unsafe_allow_html=True)

st.title('Visor de cobertura vegetal por microcuenca')
st.caption('Caracterización de cobertura vegetal · Clases MapBiomas · Áreas de intersección calculadas en EPSG:32718')

try:
    micros = inventario(str(BD), BD.stat().st_mtime)
    paleta = colores_globales(str(BD), BD.stat().st_mtime)
except Exception as exc:
    st.exception(exc)
    st.stop()

uhs = sorted(micros['ID_UH'].unique().tolist())
if 'cob_uh_actual' not in st.session_state:
    st.session_state.cob_uh_actual = 1 if 1 in uhs else uhs[0]
if 'cob_id_actual' not in st.session_state:
    st.session_state.cob_id_actual = None
if 'cob_vista_detalle' not in st.session_state:
    st.session_state.cob_vista_detalle = False

with st.sidebar:
    st.header('Consulta')
    uh_id = st.selectbox('Unidad hidrográfica', uhs, format_func=lambda v: UH_NOMBRES.get(v, str(v)), key='cob_uh_actual')
    uh = UH_NOMBRES.get(uh_id, str(uh_id))
    ids = sorted(micros.loc[micros.ID_UH == uh_id, 'ID_MICRO'].unique().tolist())
    st.caption(f'{len(ids):,} microcuencas disponibles')
    buscar = st.text_input('Buscar ID_MICRO', placeholder='Ej. 200005')
    if st.button('Buscar microcuenca', use_container_width=True):
        try:
            mid_buscado = int(buscar.strip())
            if mid_buscado in ids:
                st.session_state.cob_id_actual = mid_buscado
                st.session_state.cob_vista_detalle = True
                st.rerun()
            else:
                st.warning('El ID no pertenece a la unidad hidrográfica seleccionada.')
        except ValueError:
            st.warning('Introduce un identificador numérico.')
    if st.button('Mostrar todas las microcuencas', use_container_width=True):
        st.session_state.cob_vista_detalle = False
        st.session_state.cob_id_actual = None
        st.rerun()
    st.divider()
    st.subheader('Cartografía')
    base = st.selectbox('Mapa base', list(TILES), index=0)
    st.caption('Los mapas base requieren internet; los datos temáticos y las microcuencas se consultan en archivos locales.')
    st.divider()
    st.caption('Haz clic en una microcuenca del mapa general o utiliza el buscador.')

if st.session_state.cob_id_actual not in ids:
    st.session_state.cob_id_actual = None
    st.session_state.cob_vista_detalle = False

if not st.session_state.cob_vista_detalle:
    st.subheader(f'Microcuencas de {uh}')
    st.caption('Haz clic dentro de una microcuenca para abrir su ficha de cobertura vegetal.')
    try:
        general = microcuencas_uh(str(MICRO), MICRO.stat().st_mtime, uh_id)
    except Exception as exc:
        st.exception(exc)
        st.stop()
    if general.empty:
        st.warning('No hay microcuencas disponibles para esta unidad hidrográfica.')
        st.stop()
    mapa = crear_mapa(base)
    folium.GeoJson(
        general.to_json(), name='Microcuencas',
        style_function=lambda f: {'fillColor':'#6ea9a0','fillOpacity':0.25,'color':'#4d817c','weight':0.65},
        highlight_function=lambda f: {'fillColor':'#e6b85c','fillOpacity':0.60,'color':'#b67522','weight':2},
        tooltip=folium.GeoJsonTooltip(fields=['ID_MICRO'], aliases=['ID_MICRO:'], sticky=True),
    ).add_to(mapa)
    minx, miny, maxx, maxy = general.total_bounds
    mapa.fit_bounds([[miny, minx], [maxy, maxx]])
    evento = st_folium(mapa, height=660, use_container_width=True,
                       key=f'cob_general_{uh_id}_{base}', returned_objects=['last_object_clicked'])
    click = evento.get('last_object_clicked') if evento else None
    if click and 'lat' in click and 'lng' in click:
        punto = Point(click['lng'], click['lat'])
        posibles = general[general.geometry.intersects(punto)]
        if not posibles.empty:
            st.session_state.cob_id_actual = int(posibles.iloc[0]['ID_MICRO'])
            st.session_state.cob_vista_detalle = True
            st.rerun()
else:
    mid = int(st.session_state.cob_id_actual)
    try:
        micro = microcuenca_detalle(str(MICRO), MICRO.stat().st_mtime, mid)
        resumen = resumen_micro(str(BD), BD.stat().st_mtime, mid)
        control = control_micro(str(BD), BD.stat().st_mtime, mid)
        geom = cobertura_micro(str(GEOM), GEOM.stat().st_mtime, mid)
    except Exception as exc:
        st.exception(exc)
        st.stop()
    if micro.empty or control.empty:
        st.error('No se encontró la geometría o el control de la microcuenca.')
        st.stop()
    registro = control.iloc[0]
    area = float(registro['AREA_MICRO_KM2'])
    cobertura_pct = float(registro['PORC_COBERTURA'])
    estado = str(registro['ESTADO'])
    st.subheader(f'Microcuenca {mid} · {uh}')
    c1, c2, c3 = st.columns(3)
    c1.metric('Área de microcuenca', f'{area:,.3f} km²')
    c2.metric('Clases de cobertura vegetal', f'{int(registro["N_CLASES"]):,}')
    c3.metric('Cobertura clasificada', f'{cobertura_pct:.2f} %')
    if float(registro['AREA_SIN_DATOS_KM2']) > 0:
        st.info(f'Esta microcuenca presenta {float(registro["AREA_SIN_DATOS_KM2"]):.6f} km² sin clasificación. No se asigna una clase artificial a esas superficies.')
    if cobertura_pct > 100.01:
        st.warning('La cobertura clasificada supera el 100 %. Revisar el control espacial.')
    if not resumen.empty:
        resumen = resumen.copy()
        resumen['ID_COB'] = resumen['ID_COB'].astype(int)
        resumen['COLOR'] = resumen['ID_COB'].map(paleta)
        resumen['CATEGORIA'] = resumen['ID_COB'].astype(str) + ' · ' + resumen['CLASE'].astype(str)
        resumen['PORC_TEXTO'] = resumen['PORCENTAJE'].map(lambda x: f'{x:.2f} %')
        distribucion = resumen.sort_values('AREA_KM2', ascending=False).reset_index(drop=True)
    else:
        distribucion = pd.DataFrame(columns=['ID_COB','CLASE','GRUPO','TIPO','COLOR','AREA_KM2','PORCENTAJE','CATEGORIA','PORC_TEXTO'])

    mapa_col, datos_col = st.columns([1.20, 1.15], gap='medium')
    with mapa_col:
        st.markdown('#### Mapa temático de cobertura vegetal')
        mapa = crear_mapa(base)
        if not geom.empty:
            geom = geom.to_crs(4326)
            # Una capa GeoJSON por clase reduce el número de objetos Folium,
            # manteniendo la geometría y atributos de cada fragmento.
            for codigo, grupo in geom.groupby('ID_COB', sort=False):
                codigo = int(codigo)
                color = paleta.get(codigo)
                if color is None:
                    st.error(f'Falta HEX_COLOR para ID_COB={codigo}.')
                    st.stop()
                datos = grupo[['ID_COB','CLASE','GRUPO','TIPO','AREA_KM2','geometry']].copy()
                datos['ID_COB'] = datos['ID_COB'].astype(str)
                datos['AREA_KM2'] = datos['AREA_KM2'].round(6)
                folium.GeoJson(
                    datos.to_json(),
                    name=str(grupo.iloc[0]['CLASE']),
                    style_function=lambda f, c=color: {'fillColor':c, 'fillOpacity':0.78, 'color':'#4a5358', 'weight':0.55},
                    highlight_function=lambda f: {'weight':2, 'color':'#172c43', 'fillOpacity':0.92},
                    tooltip=folium.GeoJsonTooltip(
                        fields=['ID_COB','CLASE','GRUPO','TIPO','AREA_KM2'],
                        aliases=['Código:','Clase:','Grupo:','Tipo:','Área (km²):'],
                        sticky=True,
                        localize=True,
                        style=(
                            'font-family:Arial,sans-serif;'
                            'font-size:13px;'
                            'line-height:1.35;'
                            'padding:5px 7px;'
                            'max-width:300px;'
                        )
                    ),
                ).add_to(mapa)
        limite = micro.to_crs(4326)
        folium.GeoJson(
            limite.to_json(), name='Límite de microcuenca',
            style_function=lambda f: {'fillOpacity':0,'color':'#202f39','weight':2.5},
            interactive=False
        ).add_to(mapa)
        minx, miny, maxx, maxy = limite.total_bounds
        dx, dy = max(maxx-minx, 0.002), max(maxy-miny, 0.002)
        mapa.fit_bounds([[miny-dy*0.14, minx-dx*0.14], [maxy+dy*0.14, maxx+dx*0.14]])
        st_folium(mapa, height=625, use_container_width=True,
                  key=f'cob_detalle_{mid}_{base}', returned_objects=[])
        st.caption('Pasa el cursor sobre cada fragmento para consultar su clase, grupo, tipo y área.')

    with datos_col:
        st.markdown('#### Distribución de cobertura vegetal')
        if distribucion.empty:
            st.info('No se encontraron clases de cobertura vegetal para esta microcuenca.')
        else:
            orden = distribucion['CATEGORIA'].tolist()
            limite_x = max(105, float(distribucion['PORCENTAJE'].max()) * 1.20 + 3)
            barras = alt.Chart(distribucion).mark_bar(size=24, cornerRadiusEnd=3).encode(
                x=alt.X('PORCENTAJE:Q', title='Porcentaje de microcuenca (%)', scale=alt.Scale(domain=[0, limite_x])),
                y=alt.Y('CATEGORIA:N', sort=orden, title=None, axis=alt.Axis(labelLimit=240, labelFontSize=11)),
                color=alt.Color('CATEGORIA:N', scale=alt.Scale(domain=orden, range=distribucion['COLOR'].tolist()), legend=None),
                tooltip=[alt.Tooltip('ID_COB:Q', title='Código'), alt.Tooltip('CLASE:N', title='Clase'),
                         alt.Tooltip('GRUPO:N', title='Grupo'), alt.Tooltip('TIPO:N', title='Tipo'),
                         alt.Tooltip('AREA_KM2:Q', format='.4f', title='Área (km²)'),
                         alt.Tooltip('PORCENTAJE:Q', format='.2f', title='Porcentaje (%)')]
            )
            etiquetas = alt.Chart(distribucion).mark_text(align='left', baseline='middle', dx=7, fontSize=12, fontWeight='bold').encode(
                x=alt.X('PORCENTAJE:Q', scale=alt.Scale(domain=[0, limite_x])),
                y=alt.Y('CATEGORIA:N', sort=orden), text='PORC_TEXTO:N', color=alt.value('#B7C2CC')
            )
            grafico = (barras + etiquetas).properties(height=max(280, len(distribucion)*52)).configure_view(strokeWidth=0)
            st.altair_chart(grafico, use_container_width=True)
            with st.expander('Leyenda y distribución', expanded=True):
                for _, r in distribucion.iterrows():
                    nombre = html.escape(f'{int(r["ID_COB"])} · {r["CLASE"]} — {r["GRUPO"]} / {r["TIPO"]}')
                    st.markdown(f'<div class="legend-row"><span class="legend-swatch" style="background:{r["COLOR"]}"></span><span>{nombre} — <b>{r["PORCENTAJE"]:.2f}%</b></span></div>', unsafe_allow_html=True)

    st.markdown('##### Detalle de cobertura vegetal por clase')
    if resumen.empty:
        st.info('No hay datos de cobertura vegetal para esta microcuenca.')
    else:
        filas = []
        for _, r in resumen.iterrows():
            color = paleta[int(r['ID_COB'])]
            filas.append(
                '<tr>'
                f'<td><span class="cob-dot" style="background:{color}"></span><strong>{int(r["ID_COB"])}</strong></td>'
                f'<td>{html.escape(str(r["CLASE"]))}</td>'
                f'<td>{html.escape(str(r["GRUPO"]))}</td>'
                f'<td>{html.escape(str(r["TIPO"]))}</td>'
                f'<td class="cob-num">{float(r["AREA_KM2"]):.4f}</td>'
                f'<td class="cob-num">{float(r["PORCENTAJE"]):.2f} %</td>'
                '</tr>'
            )
        encabezado = ('<table class="cob-table"><thead><tr><th>Código</th><th>Clase</th>'
                      '<th>Grupo</th><th>Tipo</th><th>Área (km²)</th><th>% microcuenca</th>'
                      '</tr></thead><tbody>')
        st.markdown(encabezado + ''.join(filas) + '</tbody></table>', unsafe_allow_html=True)
        tabla_csv = resumen[['ID_COB','CLASE','GRUPO','TIPO','HEX_COLOR','AREA_M2','AREA_KM2','PORCENTAJE']].copy()
        st.download_button('Descargar distribución CSV',
                           data=tabla_csv.to_csv(index=False).encode('utf-8-sig'),
                           file_name=f'cobertura_vegetal_micro_{mid}.csv',
                           mime='text/csv', use_container_width=True)

    st.caption('Fuente temática: MapBiomas. Superficies calculadas por intersección espacial en EPSG:32718. Colores originales del campo HEX_COLOR.')
    with st.expander('Información técnica y control de calidad', expanded=False):
        st.write(f'Estado de control: {estado}. Las superficies sin datos permanecen sin clasificación.')
        st.dataframe(control, hide_index=True, use_container_width=True)
        st.write(f'Fragmentos espaciales visualizados: {len(geom):,}')
        st.write(f'Área sin clasificación: {float(registro["AREA_SIN_DATOS_KM2"]):.6f} km²')
