import pandas as pd
import numpy as np
import json
import os
import re
import requests
import streamlit as st
from geopy.distance import geodesic
from geopy.geocoders import ArcGIS, Nominatim, Photon
from datetime import datetime, timedelta
from ortools.constraint_solver import routing_enums_pb2
from ortools.constraint_solver import pywrapcp
import requests
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import streamlit as st

import requests
import math
import numpy as np
from functools import lru_cache

def _fmt_coords_osrm(coords):
    # coords: [(lat, lon), ...]
    # OSRM quer "lon,lat;lon,lat"
    return ";".join([f"{lon:.6f},{lat:.6f}" for lat, lon in coords])

def _fetch_osrm_table(base_url: str, coords, timeout=12):
    """
    Chama OSRM Table e retorna matriz NxN de durações em MINUTOS (float).
    base_url exemplo:
      - público: "https://router.project-osrm.org"
      - privado: "https://seu-osrm.com" (sem / no final)
    """
    coord_str = _fmt_coords_osrm(coords)
    url = f"{base_url.rstrip('/')}/table/v1/driving/{coord_str}"
    params = {"annotations": "duration"}  # segundos
    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if "durations" not in data or data["durations"] is None:
        raise RuntimeError("OSRM retornou sem 'durations'")
    # converte seg -> min
    dur = np.array(data["durations"], dtype=float) / 60.0
    # OSRM pode retornar null em células não conectadas
    if np.isnan(dur).any():
        # troca nan por um número grande (inviável)
        dur = np.nan_to_num(dur, nan=1e9)
    return dur

def _stitch_table_in_chunks(base_url: str, coords, chunk=90, timeout=12):
    """
    OSRM público costuma ter limite ~100 coords.
    Faz matriz completa em blocos (sources/destinations implícitos por sublistas).
    Implementação simples: monta matriz por blocos NxN chamando table em sublista
    (isso repete depot em múltiplos blocos se necessário; bom o suficiente).
    """
    n = len(coords)
    M = np.full((n, n), 1e9, dtype=float)

    # divide indices em blocos
    blocks = [list(range(i, min(i + chunk, n))) for i in range(0, n, chunk)]

    for bi in blocks:
        sub_coords_i = [coords[k] for k in bi]
        # para preencher M[bi, bj], chamamos table com coords = bi + bj
        # mas OSRM table sempre retorna NxN; vamos fazer uma chamada por par de blocos.
        for bj in blocks:
            sub_coords_j = [coords[k] for k in bj]
            # cria lista combinada
            comb = sub_coords_i + sub_coords_j
            # chama table
            T = _fetch_osrm_table(base_url, comb, timeout=timeout)
            # pega quadrante i->j (linhas 0..len(bi)-1, colunas len(bi)..)
            a = 0
            b = len(bi)
            c = len(bi)
            d = len(bi) + len(bj)
            M[np.ix_(bi, bj)] = T[a:b, c:d]

    # diagonal 0
    np.fill_diagonal(M, 0.0)
    return M

def get_time_matrix(coords, osrm_public_url, osrm_private_url=None, timeout=12):
    """
    Tenta público; se der erro, tenta privado; se ambos falharem, levanta exceção.
    Retorna matriz em minutos (numpy array).
    """
    n = len(coords)
    if n < 2:
        return np.zeros((n, n), dtype=float)

    # tenta OSRM público
    try:
        if n <= 90:
            return _fetch_osrm_table(osrm_public_url, coords, timeout=timeout)
        else:
            return _stitch_table_in_chunks(osrm_public_url, coords, chunk=90, timeout=timeout)
    except Exception:
        if not osrm_private_url:
            raise

    # fallback: privado
    if osrm_private_url:
        if n <= 200:  # seu privado pode permitir mais; ajuste se quiser
            return _fetch_osrm_table(osrm_private_url, coords, timeout=timeout)
        else:
            return _stitch_table_in_chunks(osrm_private_url, coords, chunk=180, timeout=timeout)

    raise RuntimeError("Sem OSRM disponível")

def montar_pontos_da_rota(df, rota_alvo):
    m = df["ROTA"].astype(str).str.strip().str.upper() == str(rota_alvo).strip().upper()
    dfr = df.loc[m].copy()
    if dfr.empty:
        return None, "Rota vazia."

    # ✅ NÃO calcular/roteirizar quem tem HORARIO = 'XXXXX'
    if "HORARIO" in dfr.columns:
        is_x = dfr["HORARIO"].astype(str).str.strip().str.upper() == "XXXXX"
        dfr = dfr.loc[~is_x].copy()
        if dfr.empty:
            return None, "Rota só tem 'XXXXX' (nada para calcular)."

    # ordenação determinística
    dfr["ORDEM_NUM"] = pd.to_numeric(dfr.get("ORDEM", np.nan), errors="coerce")
    dfr = dfr.sort_values(["ORDEM_NUM"], kind="mergesort")

    lat = pd.to_numeric(dfr.get("LATITUDE EMBARQUE"), errors="coerce")
    lon = pd.to_numeric(dfr.get("LONGITUDE EMBARQUE"), errors="coerce")

    faltam = lat.isna() | lon.isna()
    if faltam.any():
        ex = dfr.loc[faltam, ["NOME", "ORDEM"]].head(10).to_dict("records")
        return None, f"Faltam coords de embarque em alguns pontos (ex.: {ex})"

    pontos = list(zip(lat.tolist(), lon.tolist()))
    return (dfr, pontos), "OK"

def simular_rota(df, rota_alvo, destino_latlon, hora_alvo, parada_min):
    df2 = df.copy()
    if "HORARIO_PREV" not in df2.columns:
        df2["HORARIO_PREV"] = ""

    # ✅ limpa preview para quem é XXXXX (não deve aparecer calculado)
    m = df2["ROTA"].astype(str).str.strip().str.upper() == str(rota_alvo).strip().upper()
    if "HORARIO" in df2.columns:
        is_x_all = df2["HORARIO"].astype(str).str.strip().str.upper() == "XXXXX"
        df2.loc[m & is_x_all, "HORARIO_PREV"] = ""
        
    pack, msg = montar_pontos_da_rota(df2, rota_alvo)
    if pack is None:
        return df2, msg

    dfr, pontos = pack
    prev, durs = calcular_horarios_osrm_backwards(pontos, destino_latlon, hora_alvo, parada_min)
    df2.loc[dfr.index, "HORARIO_PREV"] = prev
    return df2, "OK"
def aplicar_so_vazios(df, rota_alvo):
    df2 = df.copy()

    m = df2["ROTA"].astype(str).str.strip().str.upper() == str(rota_alvo).strip().upper()
    hor = df2.get("HORARIO", "")

    vazio = hor.isna() | (hor.astype(str).str.strip() == "")
    tem_prev = df2.get("HORARIO_PREV", "").astype(str).str.strip() != ""

    mask = m & vazio & tem_prev
    df2.loc[mask, "HORARIO"] = df2.loc[mask, "HORARIO_PREV"]
    return df2, int(mask.sum())
def aplicar_todos(df, rota_alvo):
    df2 = df.copy()
    m = df2["ROTA"].astype(str).str.strip().str.upper() == str(rota_alvo).strip().upper()
    tem_prev = df2.get("HORARIO_PREV", "").astype(str).str.strip() != ""
    mask = m & tem_prev
    df2.loc[mask, "HORARIO"] = df2.loc[mask, "HORARIO_PREV"]
    return df2, int(mask.sum())

# ==============================================================================
# CONFIGURAÇÕES E CACHE
# ==============================================================================
FILE_CACHE = 'cache_enderecos.json'
CACHE_MEMORIA = None
FILE_CORRECOES = 'memoria_correcoes.json'

def load_json(arquivo, default=None):
    if not os.path.exists(arquivo): return default if default is not None else {}
    try:
        with open(arquivo, 'r', encoding='utf-8') as f: return json.load(f)
    except: return default if default is not None else {}

# ==============================================================================
# 5. INTEGRAÇÃO OSRM (TRAJETO REAL E TEMPO)
# ==============================================================================
CACHE_TRAJETOS = "cache_trajetos.json"

def decodificar_polyline(polyline_str):
    """Decodifica a string de geometria do OSRM para lista de [lat, lon]"""
    index, lat, lng = 0, 0, 0
    coordinates = []
    changes = {'latitude': 0, 'longitude': 0}
    length = len(polyline_str)
    while index < length:
        for unit in ['latitude', 'longitude']:
            shift, result = 0, 0
            while True:
                byte = ord(polyline_str[index]) - 63
                index += 1
                result |= (byte & 0x1f) << shift
                shift += 5
                if not byte >= 0x20: break
            if (result & 1): changes[unit] = ~(result >> 1)
            else: changes[unit] = (result >> 1)
        lat += changes['latitude']
        lng += changes['longitude']
        coordinates.append([lat / 100000.0, lng / 100000.0])
    return coordinates


@st.cache_data(ttl=600)
def _osrm_legs_duration_seconds(coords_lonlat_str: str):
    url = f"https://router.project-osrm.org/route/v1/driving/{coords_lonlat_str}?overview=false&steps=false"
    r = requests.get(url, timeout=25)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != "Ok":
        raise RuntimeError(f"OSRM erro: {data.get('code')} {data.get('message','')}")
    legs = data["routes"][0]["legs"]
    return [leg["duration"] for leg in legs]  # segundos

def calcular_horarios_osrm_backwards(pontos_latlon, destino_latlon, hora_chegada_alvo="06:37", parada_min=0):
    # monta coords lon,lat
    coords = ";".join([f"{lon},{lat}" for (lat, lon) in pontos_latlon] + [f"{destino_latlon[1]},{destino_latlon[0]}"])
    durs = _osrm_legs_duration_seconds(coords)

    # âncora no destino
    t = datetime.strptime(hora_chegada_alvo, "%H:%M")
    horarios = [None] * len(pontos_latlon)

    for i in range(len(durs) - 1, -1, -1):
        t -= timedelta(seconds=float(durs[i]))
        t -= timedelta(minutes=float(parada_min))
        horarios[i] = t.strftime("%H:%M")

    return horarios, durs

def get_rota_osrm(coordenadas):
    """
    Recebe lista de tuplas [(lat, lon), (lat, lon)...]
    Retorna: geometria (encoded), duração (minutos), distancia (metros)
    """
    # Formata para "lon,lat;lon,lat"
    coords_str = ";".join([f"{lon},{lat}" for lat, lon in coordenadas])
    url = f"http://router.project-osrm.org/route/v1/driving/{coords_str}?overview=full&geometries=polyline"
    
    try:
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            res = r.json()
            if res['code'] == 'Ok':
                rota = res['routes'][0]
                geometry = rota['geometry']
                duration_s = rota['duration']
                distance_m = rota['distance']
                return geometry, duration_s / 60, distance_m
    except Exception as e:
        print(f"Erro OSRM: {e}")
    return None, 0, 0

def salvar_trajeto_cache(nome_rota, geometry, tempo_total):
    cache = load_json(CACHE_TRAJETOS, {})
    cache[str(nome_rota).upper()] = {
        "geometry": geometry,
        "tempo_min": tempo_total,
        "atualizado_em": datetime.now().strftime("%d/%m/%Y %H:%M")
    }
    save_json(CACHE_TRAJETOS, cache)

def inserir_e_otimizar_osrm(df_geral, idx_novo, nome_rota, lat_dest, lon_dest, hora_chegada_target="06:37",modo_saida=False ):
    """
    1. Encaixa o passageiro na melhor posição (Geodesic - Rápido).
    2. Recalcula a rota final com OSRM (Preciso).
    3. Atualiza horários de TODOS baseado no tempo real do OSRM.
    4. Salva o desenho no cache.
    """
    # A. Preparação dos dados
    mask = df_geral['ROTA'] == nome_rota
    df_rota = df_geral[mask].copy()
    
    # Separa o novato dos antigos
    pax_antigos = df_rota[df_rota.index != idx_novo].copy()
    
    def _eh_xxxxx(v):
        return 'X' in str(v).upper()

    if not modo_saida:
        # ENTRADA: remove passageiros XXXXX do cálculo
        pax_antigos = pax_antigos[~pax_antigos['HORARIO'].apply(_eh_xxxxx)]

    # Ordena os antigos pelo horário existente (preserva a sequência atual)
   # ==========================================================
# CORREÇÃO: ordenar pela ORDEM (não pelo HORARIO)
# ==========================================================

    if 'ORDEM' in pax_antigos.columns:
        pax_antigos['ORDEM_NUM'] = pd.to_numeric(
            pax_antigos['ORDEM'], errors='coerce'
        ).fillna(10**9)

        pax_antigos = pax_antigos.sort_values(
            'ORDEM_NUM',
            kind='mergesort'  # estável, mantém ordem original
        )
    else:
        # Fallback de segurança (não deveria acontecer no seu sistema)
        pax_antigos = pax_antigos.copy()

    
    sequencia = []
    for idx, row in pax_antigos.iterrows():
        lat = row['LATITUDE EMBARQUE'] if row['LATITUDE EMBARQUE'] != 0 else row['LATITUDE CASA']
        lon = row['LONGITUDE EMBARQUE'] if row['LONGITUDE EMBARQUE'] != 0 else row['LONGITUDE CASA']
        sequencia.append({'id': idx, 'lat': lat, 'lon': lon})

    # Dados do Novato
    lat_novo = df_geral.at[idx_novo, 'LATITUDE EMBARQUE']
    lon_novo = df_geral.at[idx_novo, 'LONGITUDE EMBARQUE']
    
    if lat_novo == 0: return df_geral # Sem GPS, aborta

    # B. Encontra a melhor posição (Inserção Mais Barata - Geodesic)
    melhor_posicao = 0
    menor_custo = float('inf')
    
    # Testa inserir em cada brecha
    for i in range(len(sequencia) + 1):
        p_antes = sequencia[i-1] if i > 0 else None
        p_depois = sequencia[i] if i < len(sequencia) else {'lat': lat_dest, 'lon': lon_dest}
        
        custo = 0
        if p_antes: custo += geodesic((p_antes['lat'], p_antes['lon']), (lat_novo, lon_novo)).meters
        if p_depois: custo += geodesic((lat_novo, lon_novo), (p_depois['lat'], p_depois['lon'])).meters
            
        if custo < menor_custo:
            menor_custo = custo
            melhor_posicao = i

    # Insere na lista
    sequencia.insert(melhor_posicao, {'id': idx_novo, 'lat': lat_novo, 'lon': lon_novo})
    # ✅ GRAVA ORDEM pela sequência final (1..N)
    if 'ORDEM' in df_geral.columns:
        for pos, no in enumerate(sequencia, start=1):
            df_geral.at[no['id'], 'ORDEM'] = int(pos)

    # ✅ NOVO: grava ORDEM pela sequência final (1..N)
    for pos, no in enumerate(sequencia, start=1):
        df_geral.at[no['id'], 'ORDEM'] = int(pos)
    
    # C. Chama OSRM para a rota COMPLETA
    coords_osrm = [(p['lat'], p['lon']) for p in sequencia]
    coords_osrm.append((lat_dest, lon_dest)) # Adiciona fábrica no final
    
    geo_str, tempo_total_min, dist_total_m = get_rota_osrm(coords_osrm)
    
    # D. Se o OSRM funcionar, usamos o tempo dele. Se falhar, usamos o cálculo antigo.
    if geo_str:
        # Salva o desenho
        salvar_trajeto_cache(nome_rota, geo_str, tempo_total_min)
        
        # Recalcula horários (Backwards com o tempo total do OSRM)
        # Nota: O OSRM gratuito dá o tempo total, não entre pontos individuais facilmente sem chamadas complexas.
        # Para simplificar e ser robusto: Usamos o tempo total para validar, mas distribuimos proporcionalmente ou mantemos o cálculo reverso seguro.
        
        # Vamos usar o cálculo reverso seguro (Geodesic) mas ajustado por um fator de trânsito (1.3x) para ficar realista
        velocidade_real = 400 # m/min (mais lento que 500, pois é urbano real)
        tempo_embarque = 2
        
        try: h_target = datetime.strptime(str(hora_chegada_target).strip(), "%H:%M")
        except: h_target = datetime.strptime("06:37", "%H:%M")

        ref_lat, ref_lon = lat_dest, lon_dest
        
        for no in reversed(sequencia):
            dist_m = geodesic((no['lat'], no['lon']), (ref_lat, ref_lon)).meters
            # Fórmula ajustada para realidade urbana
            minutos = (dist_m / velocidade_real) + tempo_embarque
            h_target = h_target - timedelta(minutes=int(minutos))
            
            df_geral.at[no['id'], 'HORARIO'] = h_target.strftime("%H:%M")
            ref_lat, ref_lon = no['lat'], no['lon']
            
    else:
        # Fallback se OSRM falhar (seu código antigo)
        return inserir_ponto_cirurgico_por_ordem(df_geral, idx_novo, nome_rota, lat_dest, lon_dest, hora_chegada_target)

    return df_geral

def inserir_por_vizinho_adjacente(df_geral, idx_novo, rota, idx_vizinho):
    """
    Insere idx_novo logo depois do idx_vizinho (mesma rota),
    abrindo espaço (shift) nas ORDEM >= ordem_vizinho+1.
    Não mexe em horários.
    """
    df = df_geral.copy()
    rota = str(rota).strip().upper()

    # garante ORDEM numérica
    if 'ORDEM' not in df.columns:
        df['ORDEM'] = 0
    df['ORDEM'] = pd.to_numeric(df['ORDEM'], errors='coerce').fillna(0).astype(int)

    m_rota = df['ROTA'].astype(str).str.strip().str.upper() == rota
    if not m_rota.any():
        df.at[idx_novo, 'ORDEM'] = 1
        return df

    if idx_vizinho not in df.index:
        # fallback: joga no final
        max_ord = int(df.loc[m_rota, 'ORDEM'].max()) if m_rota.any() else 0
        df.at[idx_novo, 'ORDEM'] = max_ord + 1
        return df

    ord_v = int(df.at[idx_vizinho, 'ORDEM'])
    nova_ordem = ord_v + 1

    # shift: abre espaço na rota inteira (exceto o novo)
    df.loc[m_rota & (df['ORDEM'] >= nova_ordem) & (df.index != idx_novo), 'ORDEM'] += 1

    # coloca o novo no buraco aberto
    df.at[idx_novo, 'ORDEM'] = int(nova_ordem)

    # normaliza 1..N (opcional, mas recomendo)
    df_rota = df.loc[m_rota].sort_values('ORDEM', kind='mergesort')
    df.loc[df_rota.index, 'ORDEM'] = range(1, len(df_rota) + 1)

    return df



def save_json(arquivo, dados):
    try:
        with open(arquivo, 'w', encoding='utf-8') as f: json.dump(dados, f, indent=4)
    except Exception as e: print(f"Erro ao salvar JSON: {e}")

def normalize_key(texto):
    if not isinstance(texto, str): return ""
    return str(texto).strip().lower().replace(', ', ',').replace(' - ', '-')

# ==============================================================================
# 1. FUNÇÕES AUXILIARES DE ENDEREÇO (REVERSE E VIACEP)
# ==============================================================================

def obter_bairro_cidade(lat, lon):
    """
    Retorna (Bairro, Cidade) baseados na coordenada.
    Usada pelo Grok para preencher colunas vazias quando acha um GPS novo.
    """
    try:
        geo = Nominatim(user_agent="rot_bairro_checker_v27", timeout=3)
        loc = geo.reverse((lat, lon), exactly_one=True)
        
        bairro = ""
        cidade = ""
        
        if loc:
            ad = loc.raw.get('address', {})
            # Tenta achar o Bairro
            bairro = ad.get('suburb') or ad.get('neighbourhood') or ad.get('city_district') or ad.get('quarter') or ""
            # Tenta achar a Cidade
            cidade = ad.get('city') or ad.get('town') or ad.get('municipality') or ad.get('village') or "Curitiba"
            
        return str(bairro).upper(), str(cidade).upper()
    except:
        return "", ""

def consultar_viacep(endereco, cidade, bairro_ref="", uf="PR"):
    """
    Busca o CEP oficial usando o nome da rua e cidade.
    Se houver homônimos (várias 'Rua das Flores'), usa o 'bairro_ref' para desempatar.
    """
    try:
        # Limpa o endereço: remove números e complementos
        parte_texto = str(endereco).split(',')[0].split('-')[0]
        nome_rua = re.sub(r'\d+', '', parte_texto).strip()
        
        # ViaCEP exige no mínimo 3 caracteres
        if len(nome_rua) < 3: return None, None, None
        
        url = f"https://viacep.com.br/ws/{uf}/{cidade}/{nome_rua}/json/"
        resp = requests.get(url, timeout=3)
        
        if resp.status_code == 200:
            dados = resp.json()
            
            # CASO A: Várias ruas encontradas (Lista)
            if isinstance(dados, list) and len(dados) > 0:
                melhor_match = dados[0] # Pega a primeira por padrão
                
                # Se temos um Bairro de referência, tentamos filtrar
                if bairro_ref and len(str(bairro_ref)) > 2:
                    for item in dados:
                        b_viacep = str(item.get('bairro','')).upper()
                        b_ref = str(bairro_ref).upper()
                        if b_ref in b_viacep or b_viacep in b_ref:
                            melhor_match = item
                            print(f" ✨ [VIACEP] Bairro '{b_ref}' confirmou: {item.get('logradouro')}")
                            break
                return melhor_match.get('logradouro'), melhor_match.get('cep'), melhor_match.get('bairro')
            
            # CASO B: Uma rua só encontrada (Dicionário)
            elif isinstance(dados, dict) and 'erro' not in dados:
                 return dados.get('logradouro'), dados.get('cep'), dados.get('bairro')
                 
    except Exception as e:
        print(f"Erro silencioso ViaCEP: {e}")
    return None, None, None

def reverse_geocode(lat, lon, usar_overpass=True):
    """
    Retorna o nome da rua/esquina baseado na lat/lon.
    """
    endereco_final = "Endereço Manual"
    
    # 1. Tenta Overpass (Esquinas)
    if usar_overpass:
        try:
            overpass_url = "https://overpass-api.de/api/interpreter"
            query = f"""
            [out:json][timeout:10];
            way(around:20,{lat},{lon})["highway"];
            out tags;
            """
            resp = requests.post(overpass_url, data=query, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                ruas = set()
                for way in data.get("elements", []):
                    t = way.get("tags", {})
                    if "name" in t:
                        n = t["name"].upper()
                        if "ACESSO" not in n and "PEDESTRE" not in n: ruas.add(n)
                
                if len(ruas) >= 2:
                    l = list(ruas)
                    endereco_final = f"{l[0]} & {l[1]}"
                    return endereco_final.replace('RUA ', 'R. ').replace('AVENIDA ', 'AV. ')
        except: pass

    # 2. Tenta Nominatim (Rápido)
    try:
        geo = Nominatim(user_agent="rot_rev_v27_smart", timeout=4)
        loc = geo.reverse((lat, lon), exactly_one=True)
        if loc:
            ad = loc.raw.get('address', {})
            rua = ad.get('road', '') or ad.get('pedestrian', '') or ad.get('footway', '')
            if rua: endereco_final = rua.upper()
            else: endereco_final = loc.address.split(',')[0].upper()
            
            # 3. Backup ArcGIS
            if "UNNAMED" in endereco_final or endereco_final == "ENDEREÇO MANUAL":
                geo_arc = ArcGIS(timeout=4)
                la = geo_arc.reverse((lat, lon))
                if la: endereco_final = la.address.split(',')[0].upper()

        return endereco_final.replace('RUA ', 'R. ').replace('AVENIDA ', 'AV. ')
    except: 
        return "Endereço Manual"

# ==============================================================================
# 2. FUNÇÃO PRINCIPAL DE BUSCA GPS (INTELIGENTE)
# ==============================================================================
def buscar_gps_unico(endereco, bairro_input="", cidade="Curitiba", estado="Paraná", is_pickup=False):
    global CACHE_MEMORIA 
    if CACHE_MEMORIA is None: CACHE_MEMORIA = load_json(FILE_CACHE, {})

    # A. Coordenada Direta
    try:
        partes = str(endereco).replace(' ', '').split(',')
        if len(partes) == 2:
            lat, lon = float(partes[0]), float(partes[1])
            if -90 <= lat <= 90: return lat, lon, 1 
    except: pass

    # B. Verifica Cache
    termo_busca = f"{endereco} - {cidade}"
    key = normalize_key(termo_busca)
    if key in CACHE_MEMORIA:
        return CACHE_MEMORIA[key]['lat'], CACHE_MEMORIA[key]['lon'], CACHE_MEMORIA[key].get('tipo', 1)

    # --------------------------------------------------------------------------
    # PASSO 1: AUDITORIA COM VIACEP (ViaCEP Primeiro!)
    # --------------------------------------------------------------------------
    print(f" 🔎 [VIACEP] Auditando: '{endereco}' em '{cidade}' (Ref: {bairro_input})...")
    
    rua_oficial, cep_oficial, bairro_oficial = consultar_viacep(endereco, cidade, bairro_input, "PR")
    
    termo_final = ""
    tipo_precisao = 2
    
    if cep_oficial:
        # Extrai número
        nums = re.findall(r'\d+', str(endereco))
        num_casa = nums[-1] if nums else ""
        
        print(f" ✅ [VIACEP] Sucesso: {rua_oficial}, {num_casa} - CEP {cep_oficial}")
        # Busca Blindada: Rua Oficial + Número + CEP + Cidade
        termo_final = f"{rua_oficial}, {num_casa}, {cep_oficial}, {cidade}, Brazil"
        tipo_precisao = 1 
    else:
        # Falha no ViaCEP -> Busca textual simples
        termo_final = f"{endereco}, {cidade}, Brazil"

    # --------------------------------------------------------------------------
    # PASSO 2: GEOCODIFICAÇÃO (3 Camadas)
    # --------------------------------------------------------------------------
    geocoders = [
        ArcGIS(user_agent="rot_arc_v27", timeout=3),
        Photon(user_agent="rot_photon_v27", timeout=3),
        Nominatim(user_agent="rot_osm_v27", timeout=3)
    ]
    LAT_REF, LON_REF = -25.4284, -49.2733 

    for geo in geocoders:
        try:
            loc = geo.geocode(termo_final)
            if loc and geodesic((loc.latitude, loc.longitude), (LAT_REF, LON_REF)).km <= 100:
                CACHE_MEMORIA[key] = {'lat': loc.latitude, 'lon': loc.longitude, 'tipo': tipo_precisao}
                save_json(FILE_CACHE, CACHE_MEMORIA)
                print(f" 🎯 [GPS] Localizado via {geo.__class__.__name__}")
                return loc.latitude, loc.longitude, tipo_precisao
        except: continue

    # --------------------------------------------------------------------------
    # PASSO 3: SÓ O CEP (Backup)
    # --------------------------------------------------------------------------
    if cep_oficial:
        try:
            geo_backup = Nominatim(user_agent="rot_backup_cep", timeout=3)
            loc = geo_backup.geocode(f"{cep_oficial}, {cidade}, Brazil")
            if loc:
                 CACHE_MEMORIA[key] = {'lat': loc.latitude, 'lon': loc.longitude, 'tipo': 3} # 3 = Aproximado
                 save_json(FILE_CACHE, CACHE_MEMORIA)
                 print(" 🔸 [GPS] Usando centro do CEP (Número não encontrado)")
                 return loc.latitude, loc.longitude, 3
        except: pass

    print(" ❌ [FALHA] Endereço não encontrado.")
    return 0.0, 0.0, 0

# ==============================================================================
# 3. OTIMIZADOR (OR-TOOLS)
# ==============================================================================
#testa para ver se a ia leu este arquivo.
class OtimizadorRotas:
    """
    Resolver VRP (Simulação) com:
      - OSRM Table (público -> fallback privado)
      - Frota heterogênea (VAN/MICRO/ONIBUS) com capacidades reais
      - Parada (parada_min) somada ao tempo ao chegar em cada passageiro
      - Saída no formato: {"rotas":[...], "nao_atendidos":[...]}
    """

    def __init__(
        self,
        df_clientes,
        lat_dest,
        lon_dest,
        max_minutos=120,
        cap_veiculo=15,   # mantido por compatibilidade (não é mais a verdade da frota)
        api_key=None,
        osrm_public_url="https://router.project-osrm.org",
        osrm_private_url=None,
    ):
        self.df = df_clientes
        self.fabrica = {"lat": float(lat_dest or 0), "lon": float(lon_dest or 0)}
        self.max_minutos = int(max_minutos)
        self.cap_veiculo = int(cap_veiculo) if cap_veiculo else 15
        self.api_key = api_key

        # OSRM (público -> privado)
        self.osrm_public_url = osrm_public_url or "https://router.project-osrm.org"
        self.osrm_private_url = osrm_private_url  # você pode setar depois: otim.osrm_private_url = "https://..."

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------
    def _pick_lat_lon_row(self, row):
        """Usa EMBARQUE se existir; senão CASA."""
        lat = row.get("LATITUDE EMBARQUE", 0) or row.get("LATITUDE CASA", 0) or 0
        lon = row.get("LONGITUDE EMBARQUE", 0) or row.get("LONGITUDE CASA", 0) or 0
        try:
            return float(lat), float(lon)
        except:
            return 0.0, 0.0

    def _filtrar_df(self, incluir_sem_rota=True, filtro_rota=None, sentido="IDA"):
        """
        - sentido="IDA": remove quem tem HORARIO com X (XXXXX/folga/saida)
        - remove sem GPS
        - (opcional) filtra rota específica
        """
        df_temp = self.df.copy()

        # normaliza rota
        if "ROTA" not in df_temp.columns:
            df_temp["ROTA"] = ""
        df_temp["ROTA_NORM"] = df_temp["ROTA"].astype(str).str.strip().str.upper()

        if filtro_rota:
            alvo = str(filtro_rota).strip().upper()
            mask = df_temp["ROTA_NORM"] == alvo
            if incluir_sem_rota:
                mask = mask | df_temp["ROTA_NORM"].isin(["", "NAN", "NONE", "0", "0.0"])
            df_temp = df_temp.loc[mask].copy()
        else:
            if not incluir_sem_rota:
                df_temp = df_temp.loc[~df_temp["ROTA_NORM"].isin(["", "NAN", "NONE", "0", "0.0"])].copy()

        # sentido ida: ignora X
        if sentido and str(sentido).strip().upper() == "IDA":
            if "HORARIO" in df_temp.columns:
                hx = df_temp["HORARIO"].astype(str).str.upper().str.contains("X", na=False)
                df_temp = df_temp.loc[~hx].copy()

        # remove sem GPS
        lats = []
        lons = []
        for _, r in df_temp.iterrows():
            lat, lon = self._pick_lat_lon_row(r)
            lats.append(lat)
            lons.append(lon)
        df_temp["LAT_FINAL"] = lats
        df_temp["LON_FINAL"] = lons
        df_temp = df_temp.loc[(df_temp["LAT_FINAL"] != 0) & (df_temp["LON_FINAL"] != 0)].copy()

        return df_temp

    def _montar_locais(self, df_rota):
        """
        locais[0] = fábrica
        locais[1..] = passageiros
        """
        locais = [{
            "lat": self.fabrica["lat"],
            "lon": self.fabrica["lon"],
            "tipo": "Fabrica",
            "nome": "Fábrica",
            "id_original": None,
        }]
        for idx, row in df_rota.iterrows():
            locais.append({
                "lat": float(row["LAT_FINAL"]),
                "lon": float(row["LON_FINAL"]),
                "tipo": "Pax",
                "nome": str(row.get("NOME", "")).strip() or f"Pax {idx}",
                "id_original": idx,
            })
        return locais

    def _inferir_frota_se_vazia(self, n_pax, qtd_vans, qtd_micros, qtd_onibus):
        """
        Se a UI não passar quantidade, usamos VAN suficiente pra cobrir todo mundo.
        """
        qtd_vans = int(qtd_vans or 0)
        qtd_micros = int(qtd_micros or 0)
        qtd_onibus = int(qtd_onibus or 0)

        if (qtd_vans + qtd_micros + qtd_onibus) <= 0:
            # default conservador: só vans
            qtd_vans = int(math.ceil(max(n_pax, 1) / 15.0))

        return qtd_vans, qtd_micros, qtd_onibus

    def _criar_frota(self, qtd_vans, qtd_micros, qtd_onibus):
        """
        Retorna:
          - capacities (list[int]) por veículo
          - labels (list[str]) por veículo
        """
        capacities = []
        labels = []

        # VAN 15
        for i in range(int(qtd_vans)):
            capacities.append(15)
            labels.append(f"VAN {i+1:02d}")

        # MICRO 28
        for i in range(int(qtd_micros)):
            capacities.append(28)
            labels.append(f"MICRO {i+1:02d}")

        # ÔNIBUS 48
        for i in range(int(qtd_onibus)):
            capacities.append(48)
            labels.append(f"ONIBUS {i+1:02d}")

        return capacities, labels

    def _time_matrix_osrm(self, coords_latlon, timeout=12):
        """
        coords_latlon: [(lat,lon), ...]
        Retorna matriz minutos (float) via OSRM Table, com fallback:
          público -> privado
        """
        return get_time_matrix(
            coords_latlon,
            osrm_public_url=self.osrm_public_url,
            osrm_private_url=self.osrm_private_url,
            timeout=timeout,
        )

    # -------------------------------------------------------------------------
    # ✅ NOVO: resolver() — usado pelo seu grok2.py (Rotas / Simulação)
    # -------------------------------------------------------------------------
    def resolver(
        self,
        incluir_sem_rota=True,
        filtro_rota=None,
        sentido="IDA",
        parada_min=0,
        qtd_vans=0,
        qtd_micros=0,
        qtd_onibus=0,
        solver_time_limit_s=20,
        osrm_timeout_s=12,
        penalty_nao_atendido=2_000_000,
    ):
        """
        Saída:
          {
            "rotas": [
              {
                "veiculo": "VAN 01",
                "cap_veiculo": 15,
                "total_pax": 12,
                "tempo_total": 98,
                "estourou_tempo": false,
                "pontos": [
                  {"nome": "...", "lat": ..., "lon": ..., "id_original": ..., "tempo_chegada_min": 12},
                  ...
                ]
              }, ...
            ],
            "nao_atendidos": [
              {"nome": "...", "id_original": ..., "lat": ..., "lon": ...},
              ...
            ]
          }
        """

        # 1) filtra passageiros válidos
        df_rota = self._filtrar_df(
            incluir_sem_rota=incluir_sem_rota,
            filtro_rota=filtro_rota,
            sentido=sentido,
        )

        if df_rota.empty:
            return {"rotas": [], "nao_atendidos": []}

        locais = self._montar_locais(df_rota)
        n_nodes = len(locais)
        n_pax = n_nodes - 1

        # 2) frota
        qtd_vans, qtd_micros, qtd_onibus = self._inferir_frota_se_vazia(
            n_pax, qtd_vans, qtd_micros, qtd_onibus
        )
        capacities, labels = self._criar_frota(qtd_vans, qtd_micros, qtd_onibus)
        n_vehicles = len(capacities)

        if n_vehicles <= 0:
            return {"rotas": [], "nao_atendidos": locais[1:]}  # tudo não atendido

        # 3) matriz de tempo (OSRM Table)
        coords = [(p["lat"], p["lon"]) for p in locais]  # lat,lon
        try:
            mat = self._time_matrix_osrm(coords, timeout=osrm_timeout_s)
        except Exception as e:
            # fallback final: usa sua matriz geodesic antiga (não quebra a UI)
            # (mesma lógica do seu código atual) :contentReference[oaicite:2]{index=2}
            tamanho = len(locais)
            mat = np.zeros((tamanho, tamanho), dtype=float)
            vel_media_km_min = 35 / 60
            for i in range(tamanho):
                for j in range(tamanho):
                    if i == j:
                        mat[i][j] = 0.0
                    else:
                        p1 = (locais[i]["lat"], locais[i]["lon"])
                        p2 = (locais[j]["lat"], locais[j]["lon"])
                        dist_km = geodesic(p1, p2).km
                        tempo_min = ((dist_km / vel_media_km_min) + 1) * 1.2
                        mat[i][j] = float(tempo_min)

        # 4) OR-Tools model
        manager = pywrapcp.RoutingIndexManager(n_nodes, n_vehicles, 0)
        routing = pywrapcp.RoutingModel(manager)
        # ✅ NOVO: custo fixo por veículo (evita split "de graça")
        fixed_cost = 300  # ajuste fino: 200, 300, 500...
        for v in range(n_vehicles):
            routing.SetFixedCostOfVehicle(fixed_cost, v)

        # depois de routing = pywrapcp.RoutingModel(manager)

        def time_callback(from_index, to_index):
            """
            custo = tempo OSRM (min) + parada ao CHEGAR num passageiro (to_node != depot)
            """
            a = manager.IndexToNode(from_index)
            b = manager.IndexToNode(to_index)
             # ✅ REGRA NOVA: não contar destino/fábrica -> 1º passageiro
            # Isso faz o "120 min" virar: 1º passageiro -> ... -> destino final
            if a == 0 and b != 0:
                base = 0.0
            else:
                base = float(mat[a][b])

            # parada ao CHEGAR em passageiro (não no retorno ao destino)

            # adiciona parada para "chegada" em passageiro (não no retorno pro depósito)
            if b != 0:
                base += parada_min

            # OR-Tools trabalha com int
            if base >= 1e8:
                return int(1e8)
            return int(round(base))

        transit_cb = routing.RegisterTransitCallback(time_callback)
        routing.SetArcCostEvaluatorOfAllVehicles(transit_cb)

        # Time dimension (limite por veículo)
        routing.AddDimension(
            transit_cb,
            0,                      # slack
            int(self.max_minutos),  # max per vehicle
            True,                   # start cumul = 0
            "Time",
        )
        time_dim = routing.GetDimensionOrDie("Time")
        # ✅ 3) aqui
        time_dim.SetGlobalSpanCostCoefficient(1)

        # 5) Capacity dimension (demanda = 1 por pax)
        def demand_callback(from_index):
            node = manager.IndexToNode(from_index)
            return 0 if node == 0 else 1

        demand_cb = routing.RegisterUnaryTransitCallback(demand_callback)
        routing.AddDimensionWithVehicleCapacity(
            demand_cb,
            0,               # slack
            capacities,      # capacities por veículo
            True,            # start cumul = 0
            "Capacity",
        )

        # 6) Permite "dropar" (vira nao_atendidos)
        # (penalty alto -> só dropa se realmente não couber)
        for node in range(1, n_nodes):
            routing.AddDisjunction([manager.NodeToIndex(node)], int(penalty_nao_atendido))

        # 7) parâmetros de busca
        search = pywrapcp.DefaultRoutingSearchParameters()
        search.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
        search.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        search.time_limit.FromSeconds(int(solver_time_limit_s or 20))

        sol = routing.SolveWithParameters(search)
        if not sol:
            # se não resolveu, tudo vira não atendido (não quebra a UI)
            return {"rotas": [], "nao_atendidos": locais[1:]}

        # 8) extrai rotas e não atendidos
        nao_atendidos = []
        for node in range(1, n_nodes):
            idx = manager.NodeToIndex(node)
            if sol.Value(routing.NextVar(idx)) == idx:
                nao_atendidos.append({
                    "nome": locais[node]["nome"],
                    "id_original": locais[node]["id_original"],
                    "lat": locais[node]["lat"],
                    "lon": locais[node]["lon"],
                })

        rotas_out = []
        for v in range(n_vehicles):
            index = routing.Start(v)
            pontos = []
            while not routing.IsEnd(index):
                node = manager.IndexToNode(index)
                if node != 0:
                    tmin = sol.Min(time_dim.CumulVar(index))
                    pontos.append({
                        "nome": locais[node]["nome"],
                        "id_original": locais[node]["id_original"],
                        "lat": locais[node]["lat"],
                        "lon": locais[node]["lon"],
                        "tempo_chegada_min": int(tmin),
                    })
                index = sol.Value(routing.NextVar(index))

            tempo_total = sol.Min(time_dim.CumulVar(index))
            if len(pontos) > 0:
                rotas_out.append({
                    "veiculo": labels[v],
                    "cap_veiculo": int(capacities[v]),
                    "total_pax": int(len(pontos)),
                    "tempo_total": int(tempo_total),
                    "estourou_tempo": bool(tempo_total > int(self.max_minutos)),
                    "pontos": pontos,
                })

        # ordena por tempo_total pra UI ficar bonita
        rotas_out.sort(key=lambda r: (r.get("tempo_total", 0), r.get("veiculo", "")))

        return {"rotas": rotas_out, "nao_atendidos": nao_atendidos}

# ==============================================================================
# 4. FUNÇÕES DE SUPORTE (CACHE, MEMÓRIA, IMÃ)
# ==============================================================================
# Em otimizador.py
# ==================== VERSÃO MELHORADA ====================
def usar_ponto_existente_proximo(df, idx_novo, nome_rota, raio_max_caminhada=5000):
    """
    Versão BLINDADA + FILTRO "SEM XXX": 
    Não copia dados de passageiros com horário 'XXX' (Saída/Folga).
    """
    try:
        def safe_float(valor):
            try: return float(str(valor).replace(',', '.'))
            except: return 0.0

        # 1. Pega GPS do Novato
        lat_casa = safe_float(df.at[idx_novo, 'LATITUDE CASA'])
        lon_casa = safe_float(df.at[idx_novo, 'LONGITUDE CASA'])
        
        if lat_casa == 0 or lon_casa == 0: 
            return False, "Sem GPS na Casa"

        # 2. Busca Vizinhos da mesma rota
        rota_alvo = str(nome_rota).strip().upper()
        
        # Filtro A: Mesma Rota e Não é ele mesmo
        mask_rota = (df['ROTA'].astype(str).str.strip().str.upper() == rota_alvo) & (df.index != idx_novo)
        df_rota = df[mask_rota].copy()

        # >>> FILTRO B (NOVO): IGNORA VIZINHOS COM HORÁRIO 'XXX' OU 'SAIDA' <<<
        # Se o horário tiver 'X', remove da lista de candidatos
        if 'HORARIO' in df_rota.columns:
            df_rota = df_rota[~df_rota['HORARIO'].astype(str).str.upper().str.contains('X')]

        if df_rota.empty: 
            return False, f"Rota {rota_alvo} sem vizinhos válidos (Entrada)"

        # 3. Encontra o vizinho mais próximo (agora só dos 'limpos')
        melhor_ponto = None
        menor_dist_geo = float('inf')
        raio_limite = raio_max_caminhada    

        for i, r in df_rota.iterrows():
            lat_viz = safe_float(r.get('LATITUDE EMBARQUE', 0))
            lon_viz = safe_float(r.get('LONGITUDE EMBARQUE', 0))
            
            if lat_viz != 0:
                d = geodesic((lat_casa, lon_casa), (lat_viz, lon_viz)).meters
                if d < menor_dist_geo:
                    menor_dist_geo = d
                    melhor_ponto = r
                    idx_melhor = i  

        # 4. Aplica a cópia
        if melhor_ponto is not None and menor_dist_geo <= raio_limite:
            
            # Copia GPS e Endereço do Ponto
            df.at[idx_novo, 'LATITUDE EMBARQUE'] = melhor_ponto['LATITUDE EMBARQUE']
            df.at[idx_novo, 'LONGITUDE EMBARQUE'] = melhor_ponto['LONGITUDE EMBARQUE']
            df.at[idx_novo, 'EMBARQUE'] = melhor_ponto['EMBARQUE']
            
            # Copia Horário Limpo
            df.at[idx_novo, 'HORARIO'] = melhor_ponto['HORARIO']
            if 'TURNO' in melhor_ponto: df.at[idx_novo, 'TURNO'] = melhor_ponto['TURNO']
            
            # Veículo
            tipo = 'VAN'
            if 'MICRO' in rota_alvo: tipo = 'MICRO'
            elif 'ONIBUS' in rota_alvo or 'ÔNIBUS' in rota_alvo: tipo = 'ONIBUS'
            df.at[idx_novo, 'VEICULO'] = tipo
            
            # Caminhada
            dist_real = int(menor_dist_geo)
            df.at[idx_novo, 'DIST_EMBARQUE_M'] = dist_real
            df.at[idx_novo, 'TIPO_GEO'] = 1 
            
            if 'ORDEM' in df.columns and 'ORDEM' in melhor_ponto:
                df.at[idx_novo, 'ORDEM'] = melhor_ponto['ORDEM']

            nome_vizinho = str(melhor_ponto['NOME']).split()[0].title()
            #return True, f"Copiado de {nome_vizinho} ({dist_real}m)"
            return True, f"Copiado de {nome_vizinho} ({dist_real}m)", idx_melhor
            

        return False, f"Vizinho válido muito longe ({int(menor_dist_geo)}m)"

    except Exception as e:
        print(f"Erro no otimizador: {e}")
        return False, f"Erro: {e}"
    

import pandas as pd
from geopy.distance import geodesic
import pandas as pd

def inserir_por_vizinho_geografico(df_all, idx_novo, rota_alvo, lat_dest, lon_dest):
    rota = str(rota_alvo).strip().upper()
    m = df_all['ROTA'].astype(str).str.strip().str.upper() == rota
    dfr = df_all.loc[m].copy()
    if dfr.empty:
        return df_all, None, "rota vazia"

    # coords do novo (embarque já deve estar setado p/ casa)
    lat_n = float(df_all.at[idx_novo, 'LATITUDE EMBARQUE'] or 0)
    lon_n = float(df_all.at[idx_novo, 'LONGITUDE EMBARQUE'] or 0)
    if lat_n == 0 or lon_n == 0:
        return df_all, None, "novo sem coordenadas"

    # garantir ORDEM numérica para ordenar a rota
    dfr['ORDEM_NUM'] = pd.to_numeric(dfr.get('ORDEM', 0), errors='coerce').fillna(10**9).astype(float)
    dfr = dfr.sort_values('ORDEM_NUM', kind='mergesort')

    # acha vizinho mais próximo (pelo embarque, senão casa)
    best_idx = None
    best_d = float('inf')

    for idx, row in dfr.iterrows():
        if idx == idx_novo:
            continue

        lat = row.get('LATITUDE EMBARQUE', 0) or row.get('LATITUDE CASA', 0) or 0
        lon = row.get('LONGITUDE EMBARQUE', 0) or row.get('LONGITUDE CASA', 0) or 0
        try:
            lat = float(lat); lon = float(lon)
        except:
            continue
        if lat == 0 or lon == 0:
            continue

        d = geodesic((lat, lon), (lat_n, lon_n)).meters
        if d < best_d:
            best_d = d
            best_idx = idx

    if best_idx is None:
        return df_all, None, "sem vizinho com coords"

    # decide antes/depois com base em “quem está mais perto do destino”
    # (se o novo estiver MAIS longe do destino que o vizinho, tende a entrar ANTES)
    def dist_dest(idx):
        lat = df_all.at[idx, 'LATITUDE EMBARQUE'] if df_all.at[idx, 'LATITUDE EMBARQUE'] else df_all.at[idx, 'LATITUDE CASA']
        lon = df_all.at[idx, 'LONGITUDE EMBARQUE'] if df_all.at[idx, 'LONGITUDE EMBARQUE'] else df_all.at[idx, 'LONGITUDE CASA']
        return geodesic((float(lat), float(lon)), (float(lat_dest), float(lon_dest))).meters

    dd_novo = geodesic((lat_n, lon_n), (float(lat_dest), float(lon_dest))).meters
    dd_viz  = dist_dest(best_idx)

    ordem_viz = float(pd.to_numeric(df_all.at[best_idx, 'ORDEM'], errors='coerce') or 0)
    if ordem_viz <= 0:
        # se a rota está sem ORDEM válida, joga no final com um número alto
        ordem_viz = float(dfr['ORDEM_NUM'].replace(10**9, 0).max() or 0)

    # ORDEM “fracionária” para marcar posição e depois normalizar
    if dd_novo > dd_viz:
        ordem_novo_temp = ordem_viz - 0.1   # entra antes
        lado = "ANTES"
    else:
        ordem_novo_temp = ordem_viz + 0.1   # entra depois
        lado = "DEPOIS"

    df_all.at[idx_novo, 'ORDEM'] = ordem_novo_temp
    return df_all, best_idx, f"vizinho={best_idx} d={int(best_d)}m lado={lado}"

def renumerar_ordem_rota(df_all, rota_alvo):
    rota = str(rota_alvo).strip().upper()
    m = df_all['ROTA'].astype(str).str.strip().str.upper() == rota
    dfr = df_all.loc[m].copy()
    dfr['ORDEM_NUM'] = pd.to_numeric(dfr.get('ORDEM', 0), errors='coerce').fillna(10**9)
    dfr = dfr.sort_values('ORDEM_NUM', kind='mergesort')

    # regrava 1..N
    for k, idx in enumerate(dfr.index, start=1):
        df_all.at[idx, 'ORDEM'] = k
    return df_all

def corrigir_duplicados_ordem(df, rota):
    df = df.copy()
    rota_norm = str(rota).strip().upper()

    # normaliza ORDEM
    df['ORDEM'] = pd.to_numeric(df.get('ORDEM', 0), errors='coerce').fillna(0).astype(int)

    m = df['ROTA'].astype(str).str.strip().str.upper() == rota_norm
    if not m.any():
        return df

    dfr = df.loc[m].copy()

    # ordena por ORDEM e desempata pelo índice (pra saber quem é o "segundo 14")
    dfr['_tb'] = dfr.index.astype(int)
    dfr = dfr.sort_values(['ORDEM', '_tb'], kind='mergesort')

    prev = None
    for idx in dfr.index:
        cur = int(dfr.at[idx, 'ORDEM'])
        if prev is None:
            prev = cur
            continue
        if cur <= prev:
            cur = prev + 1
            dfr.at[idx, 'ORDEM'] = cur
        prev = cur

    dfr = dfr.drop(columns=['_tb'])
    df.loc[dfr.index, 'ORDEM'] = dfr['ORDEM']
    return df


def entrar_atras_do_vizinho(df_geral, idx_novo, idx_vizinho):
    df = df_geral.copy()

    # ORDEM precisa ser número
    df['ORDEM'] = pd.to_numeric(df.get('ORDEM', 0), errors='coerce').fillna(0).astype(int)

    rota = str(df.at[idx_vizinho, 'ROTA']).strip().upper()
    ord_v = int(df.at[idx_vizinho, 'ORDEM'])
    nova_ord = ord_v + 1

    m_rota = df['ROTA'].astype(str).str.strip().str.upper() == rota

    # abre espaço: tudo >= nova_ord sobe +1 (exceto o novo)
    df.loc[m_rota & (df.index != idx_novo) & (df['ORDEM'] >= nova_ord), 'ORDEM'] += 1

    # novo entra atrás
    df.at[idx_novo, 'ORDEM'] = nova_ord
    return df
  
def recalcular_rota_reversa(df_geral, nome_rota, lat_dest, lon_dest, hora_chegada_target="07:00"):
    mask = df_geral['ROTA'] == nome_rota
    df_rota = df_geral[mask].copy()
    if df_rota.empty: return df_geral

    com_gps = df_rota[ (df_rota['LATITUDE CASA'] != 0) | (df_rota['LATITUDE EMBARQUE'] != 0) ].copy()
    if com_gps.empty: return df_geral

    pontos = []
    for idx, row in com_gps.iterrows():
        lat = row['LATITUDE EMBARQUE'] if row['LATITUDE EMBARQUE'] != 0 else row['LATITUDE CASA']
        lon = row['LONGITUDE EMBARQUE'] if row['LONGITUDE EMBARQUE'] != 0 else row['LONGITUDE CASA']
        pontos.append({'id': idx, 'lat': lat, 'lon': lon})

    # Ordenação simples por distância
    ordenados = []
    mais_longe, max_dist = None, -1
    for p in pontos:
        d = geodesic((p['lat'], p['lon']), (lat_dest, lon_dest)).meters
        if d > max_dist: max_dist = d; mais_longe = p
            
    if mais_longe: ordenados.append(mais_longe); pontos.remove(mais_longe)
    
    while pontos:
        ultimo = ordenados[-1]
        proximo, min_dist = None, float('inf')
        for p in pontos:
            d = geodesic((ultimo['lat'], ultimo['lon']), (p['lat'], p['lon'])).meters
            if d < min_dist: min_dist = d; proximo = p
        if proximo: ordenados.append(proximo); pontos.remove(proximo)

    # Cálculo reverso
    velocidade_media = 500 # m/min
    tempo_embarque = 2
    try: h_target = datetime.strptime(str(hora_chegada_target).strip(), "%H:%M")
    except: h_target = datetime.strptime("07:00", "%H:%M")

    ref_lat, ref_lon = lat_dest, lon_dest
    for idx_ativo in reversed([p['id'] for p in ordenados]):
        p_atual = next(p for p in ordenados if p['id'] == idx_ativo)
        dist_m = geodesic((p_atual['lat'], p_atual['lon']), (ref_lat, ref_lon)).meters
        minutos = (dist_m / velocidade_media) + tempo_embarque
        h_target = h_target - timedelta(minutes=int(minutos))
        df_geral.at[idx_ativo, 'HORARIO'] = h_target.strftime("%H:%M")
        ref_lat, ref_lon = p_atual['lat'], p_atual['lon']
            
    return df_geral

def salvar_correcao_permanente(nome, dados):
    if not nome: return
    memoria = load_json(FILE_CORRECOES, {})
    memoria[str(nome).strip().upper()] = dados
    save_json(FILE_CORRECOES, memoria)

def aplicar_correcoes_memoria(df):
    memoria = load_json(FILE_CORRECOES, {})
    if not memoria: return df
    for i, row in df.iterrows():
        chave = str(row['NOME']).strip().upper()
        if chave in memoria:
            d = memoria[chave]
            if 'lat_c' in d:
                df.at[i, 'LATITUDE CASA'] = d['lat_c']
                df.at[i, 'LONGITUDE CASA'] = d['lon_c']
                df.at[i, 'ENDERECO'] = d['end_c']
                df.at[i, 'BAIRRO'] = d.get('bairro', df.at[i, 'BAIRRO'])
                df.at[i, 'CIDADE'] = d.get('cidade', df.at[i, 'CIDADE'])
                df.at[i, 'TIPO_GEO'] = 1
                df.at[i, 'STATUS'] = 'OK'
            if 'lat_e' in d:
                df.at[i, 'LATITUDE EMBARQUE'] = d['lat_e']
                df.at[i, 'LONGITUDE EMBARQUE'] = d['lon_e']
                df.at[i, 'EMBARQUE'] = d['end_e']
    return df

def salvar_no_cache(chave_texto, lat, lon, nome_oficial=""):
    global CACHE_MEMORIA
    if CACHE_MEMORIA is None: CACHE_MEMORIA = load_json(FILE_CACHE, {})
    
    # Chave original
    if chave_texto and len(str(chave_texto)) > 2:
        d = {'lat': lat, 'lon': lon, 'tipo': 1}
        if nome_oficial: d['nome'] = str(nome_oficial).upper().strip()
        CACHE_MEMORIA[normalize_key(chave_texto)] = d
        
    # Chave oficial
    if nome_oficial and len(str(nome_oficial)) > 2:
        k_nova = normalize_key(nome_oficial)
        if k_nova != normalize_key(chave_texto):
            CACHE_MEMORIA[k_nova] = {'lat': lat, 'lon': lon, 'tipo': 1}
            
    save_json(FILE_CACHE, CACHE_MEMORIA)

def reparar_cache_antigo():
    print("Iniciando reparo do cache...")
    try:
        cache_velho = load_json(FILE_CACHE, {})
        cache_novo = {}
        for chave_velha, dados in cache_velho.items():
            chave_limpa = normalize_key(chave_velha)
            cache_novo[chave_limpa] = dados
        save_json(FILE_CACHE, cache_novo)
    except Exception as e:
        print(f"Erro ao reparar cache: {e}")
        # Em otimizador.py (Adicione no final)
def inserir_ponto_cirurgico_por_ordem(df_geral, idx_novo, nome_rota, lat_dest, lon_dest, hora_chegada_target):
    """
    Insere o passageiro novo:
    - Define a melhor posição (geográfica)
    - Faz efeito dominó na ORDEM
    - Normaliza ORDEM para 1..N (sem duplicar)
    - Calcula HORARIO apenas do NOVO e recalcula apenas PARA TRÁS (ordem-1 ... 1)
      (não mexe em quem está na frente / horário de chegada não muda)

    ✅ Ajustes:
    - Se novo for XXXXX (só volta), entra na ORDEM e NÃO calcula horário / NÃO recalcula ninguém.
    - Âncora preferencial: próximo ponto com horário válido após o novo; senão, fábrica.
    - Âncora ignora XXXXX.
    """
    df = df_geral.copy()
    rota = str(nome_rota).strip().upper()

    # segurança: ORDEM numérica
    if 'ORDEM' not in df.columns:
        df['ORDEM'] = 0
    df['ORDEM'] = pd.to_numeric(df['ORDEM'], errors='coerce').fillna(0).astype(int)

    # filtra rota
    m_rota = df['ROTA'].astype(str).str.strip().str.upper() == rota
    dfr = df[m_rota].copy()

    # se rota vazia/1 item
    if dfr.shape[0] <= 1:
        df.at[idx_novo, 'ORDEM'] = 1

        # ✅ se for XXXXX, não calcula horário
        h_novo_raw = str(df.at[idx_novo, 'HORARIO']).upper() if 'HORARIO' in df.columns else ''
        if 'X' in h_novo_raw:
            return df

        # calcula só o HORARIO do novo (ancora = fábrica)
        try:
            h_target = datetime.strptime(str(hora_chegada_target).strip(), "%H:%M")
        except:
            h_target = datetime.strptime("06:37", "%H:%M")

        lat_n = float(df.at[idx_novo, 'LATITUDE EMBARQUE'] or 0) or float(df.at[idx_novo, 'LATITUDE CASA'] or 0)
        lon_n = float(df.at[idx_novo, 'LONGITUDE EMBARQUE'] or 0) or float(df.at[idx_novo, 'LONGITUDE CASA'] or 0)
        if lat_n and lon_n and lat_dest and lon_dest:
            try:
                mins = _osrm_min(lat_n, lon_n, lat_dest, lon_dest)
            except:
                mins = geodesic((lat_n, lon_n), (lat_dest, lon_dest)).meters / 450
            df.at[idx_novo, 'HORARIO'] = (h_target - timedelta(minutes=int(mins + 2))).strftime("%H:%M")
        return df

    # antigos (pela ORDEM atual)
    dfr_ant = dfr[dfr.index != idx_novo].copy()
    dfr_ant['ORDEM_NUM'] = pd.to_numeric(dfr_ant['ORDEM'], errors='coerce').fillna(10**9)
    dfr_ant = dfr_ant.sort_values('ORDEM_NUM', kind='mergesort')

    sequencia = []
    for i, row in dfr_ant.iterrows():
        lat = row['LATITUDE EMBARQUE'] if row.get('LATITUDE EMBARQUE', 0) != 0 else row.get('LATITUDE CASA', 0)
        lon = row['LONGITUDE EMBARQUE'] if row.get('LONGITUDE EMBARQUE', 0) != 0 else row.get('LONGITUDE CASA', 0)

        lat_f = float(lat or 0)
        lon_f = float(lon or 0)

        # ✅ ignora pontos inválidos (senão a inserção puxa pro começo)
        if lat_f == 0.0 or lon_f == 0.0:
            continue

        sequencia.append({'id': i, 'lat': lat_f, 'lon': lon_f})

        # ✅ se não tiver pontos suficientes com GPS, não dá pra achar "melhor posição"
    if len(sequencia) < 2:
        max_ord = int(pd.to_numeric(df.loc[m_rota, 'ORDEM'], errors='coerce').fillna(0).max())
        df.at[idx_novo, 'ORDEM'] = max_ord + 1
        return df


    # coords do novo
    lat_novo = df.at[idx_novo, 'LATITUDE EMBARQUE'] if df.at[idx_novo, 'LATITUDE EMBARQUE'] != 0 else df.at[idx_novo, 'LATITUDE CASA']
    lon_novo = df.at[idx_novo, 'LONGITUDE EMBARQUE'] if df.at[idx_novo, 'LONGITUDE EMBARQUE'] != 0 else df.at[idx_novo, 'LONGITUDE CASA']
    lat_novo = float(lat_novo or 0)
    lon_novo = float(lon_novo or 0)
   #if lat_novo == 0 or lon_novo == 0:
    if not lat_novo or not lon_novo or float(lat_novo) == 0.0 or float(lon_novo) == 0.0:
        # ✅ Sem GPS: ainda assim corrige ORDEM da rota sem quebrar.
        dfr_tmp = df[m_rota].copy()
        dfr_tmp['ORDEM_NUM'] = pd.to_numeric(dfr_tmp.get('ORDEM', 0), errors='coerce').fillna(0).astype(int)
        max_ord = int(dfr_tmp['ORDEM_NUM'].max()) if len(dfr_tmp) else 0

        # coloca o novo no final (não dá pra calcular melhor posição sem GPS)
        df.at[idx_novo, 'ORDEM'] = max_ord + 1

        # (opcional) garantir que não ficou duplicado por sujeira
        # não precisa reordenar "por fim", só garantir ORDEM única
        # se você não quer 1..N, pode pular normalização:
        # --- recomendo manter esta normalização porque remove duplicação ---
        dfr2 = df[m_rota].copy()
        dfr2['ORDEM'] = pd.to_numeric(dfr2.get('ORDEM', 0), errors='coerce').fillna(10**9).astype(int)
        dfr2 = dfr2.sort_values(['ORDEM'], kind='mergesort')
        df.loc[dfr2.index, 'ORDEM'] = range(1, len(dfr2) + 1)    
        return df

    # acha melhor posição geográfica (menor aumento de rota)
    melhor_pos = 0
    menor_delta = float('inf')

    def dist_m(a_lat, a_lon, b_lat, b_lon):
        return geodesic((a_lat, a_lon), (b_lat, b_lon)).meters

    for pos in range(len(sequencia) + 1):
        A = sequencia[pos - 1] if pos > 0 else None
        B = sequencia[pos] if pos < len(sequencia) else {'lat': float(lat_dest), 'lon': float(lon_dest)}

        if A is None:
            delta = dist_m(lat_novo, lon_novo, B['lat'], B['lon'])
        else:
            delta = (
                dist_m(A['lat'], A['lon'], lat_novo, lon_novo) +
                dist_m(lat_novo, lon_novo, B['lat'], B['lon']) -
                dist_m(A['lat'], A['lon'], B['lat'], B['lon'])
            )

        if delta < menor_delta:
            menor_delta = delta
            melhor_pos = pos


    # define nova ordem como posição (1..N+1)
    nova_ordem = melhor_pos + 1

    # efeito dominó (dentro da rota)
    df.loc[m_rota & (df['ORDEM'] >= nova_ordem) & (df.index != idx_novo), 'ORDEM'] += 1
    df.at[idx_novo, 'ORDEM'] = int(nova_ordem)

    # normaliza ORDEM 1..N sem duplicar
    dfr2 = df[m_rota].copy()
    dfr2['ORDEM'] = pd.to_numeric(dfr2['ORDEM'], errors='coerce').fillna(0).astype(int)
    dfr2 = dfr2.sort_values(['ORDEM'], kind='mergesort')
    df.loc[dfr2.index, 'ORDEM'] = range(1, len(dfr2) + 1)

    ordem_novo_final = int(df.at[idx_novo, 'ORDEM'])

    # ✅ se for XXXXX (só volta), entra na ORDEM e NÃO calcula horário / NÃO recalcula
    h_novo_raw = str(df.at[idx_novo, 'HORARIO']).upper() if 'HORARIO' in df.columns else ''
    if 'X' in h_novo_raw:
        return df

    # 1) calcula HORARIO do NOVO usando âncora = próximo com horário válido; senão fábrica
    try:
        h_target = datetime.strptime(str(hora_chegada_target).strip(), "%H:%M")
    except:
        h_target = datetime.strptime("06:37", "%H:%M")

    # pega lista ordenada da rota após normalizar
    dfr3 = df[m_rota].copy()
    dfr3['ORDEM_NUM'] = pd.to_numeric(dfr3['ORDEM'], errors='coerce').fillna(10**9)
    dfr3 = dfr3.sort_values('ORDEM_NUM', kind='mergesort')

    anchor_idx = None
    anchor_dt = None

    for i, row in dfr3.iterrows():
        o = int(row['ORDEM_NUM']) if row['ORDEM_NUM'] < 10**8 else None
        if o is None or o <= ordem_novo_final:
            continue

        h_raw = str(row.get('HORARIO', '')).strip().upper()

        # ✅ ignora XXXXX como âncora
        if 'X' in h_raw:
            continue

        dt = _parse_hora(h_raw)
        if dt is not None:
            anchor_idx = i
            anchor_dt = dt
            break

    if anchor_idx is None:
        # ancora = fábrica
        anchor_dt = h_target
        anchor_lat, anchor_lon = lat_dest, lon_dest
    else:
        anchor_lat, anchor_lon = _coord(df.loc[anchor_idx])

    # calcula horário do novo (novo -> âncora)
    try:
        mins = _osrm_min(lat_novo, lon_novo, anchor_lat, anchor_lon)
    except:
        mins = geodesic((lat_novo, lon_novo), (anchor_lat, anchor_lon)).meters / 450

    df.at[idx_novo, 'HORARIO'] = _fmt_hora(anchor_dt - timedelta(minutes=int(mins + 2)))

    # 2) recalcula só PARA TRÁS (ordem_novo-1 ... 1)
    if ordem_novo_final > 1:
        df = recalcular_horarios_cirurgico(df, rota, ordem_novo_final, buffer_min=2, modo_saida=False)

    return df



def resequenciar_e_recalcular_horarios(df, idx_mudou, rota_alvo, nova_ordem, lat_dest, lon_dest, h_chegada):
    # Filtra apenas a rota específica
    mask_rota = (df['ROTA'] == rota_alvo)
    
    # Aplica o efeito dominó apenas dentro desta rota
    df.loc[mask_rota & (df.index != idx_mudou) & (df['ORDEM'] >= nova_ordem), 'ORDEM'] += 1
    df.at[idx_mudou, 'ORDEM'] = nova_ordem
    
    # RESET TOTAL DA SEQUÊNCIA (Para garantir 1, 2, 3...)
    df_rota = df[mask_rota].sort_values('ORDEM')
    for i, (idx, _) in enumerate(df_rota.iterrows(), 1):
        df.at[idx, 'ORDEM'] = i
        
    return recalcular_rota_reversa(df, rota_alvo, lat_dest, lon_dest, h_chegada)
def inserir_passageiro_na_rota(df, rota_alvo, nova_ord, dados_novo: dict):
    """
    Insere um passageiro em 'rota_alvo' na posição 'nova_ord' empurrando (shift) os demais.
    - Não depende de HORARIO (XXXXX não influencia).
    - Mantém ORDEM como verdade.
    """
    df = df.copy()

    rota_alvo = str(rota_alvo).strip().upper()
    nova_ord = int(nova_ord)

    if 'ROTA' not in df.columns:
        df['ROTA'] = ""
    df['ROTA'] = df['ROTA'].astype(str).str.strip().str.upper()

    if 'ORDEM' not in df.columns:
        df['ORDEM'] = 0
    df['ORDEM'] = pd.to_numeric(df['ORDEM'], errors='coerce').fillna(0).astype(int)

    # 1) SHIFT: empurra todo mundo com ORDEM >= nova_ord
    mask = (df['ROTA'] == rota_alvo) & (df['ORDEM'] >= nova_ord)
    df.loc[mask, 'ORDEM'] = df.loc[mask, 'ORDEM'] + 1

    # 2) cria linha nova
    novo = {c: "" for c in df.columns}
    for k, v in (dados_novo or {}).items():
        if k in novo:
            novo[k] = v

    novo['ROTA'] = rota_alvo
    novo['ORDEM'] = nova_ord

    # Se quiser marcar que é novo (opcional):
    if 'NOVO' in df.columns:
        novo['NOVO'] = True

    df = pd.concat([df, pd.DataFrame([novo])], ignore_index=True)

    # 3) garante sequência limpa 1..N dentro da rota (sem usar HORARIO)
    #    (aqui não muda o “quem é quem”, só ajusta caso tenha buracos/duplicados)
    df_rota = df[df['ROTA'] == rota_alvo].copy()
    df_outros = df[df['ROTA'] != rota_alvo].copy()

    df_rota = df_rota.sort_values(['ORDEM'], kind='mergesort')
    df_rota['ORDEM'] = range(1, len(df_rota) + 1)

    df = pd.concat([df_outros, df_rota], ignore_index=True)
    return df
import pandas as pd
import requests
from datetime import datetime, timedelta

def _parse_hhmm(h):
    t = str(h).strip().upper().replace('H', ':').replace('.', ':')
    if not t or 'X' in t:
        return None
    if ':' not in t:
        return None
    p = t.split(':')
    if len(p) < 2 or (not p[0].isdigit()) or (not p[1].isdigit()):
        return None
    return datetime(2000, 1, 1, int(p[0]), int(p[1]))

def _fmt_hhmm(dt):
    return dt.strftime("%H:%M")

def _get_best_latlon(row):
    # embarque tem prioridade, se não tiver usa casa
    le = row.get('LATITUDE EMBARQUE', 0); loe = row.get('LONGITUDE EMBARQUE', 0)
    lc = row.get('LATITUDE CASA', 0); loc = row.get('LONGITUDE CASA', 0)

    try:
        if pd.notna(le) and float(le) != 0:
            return float(le), float(loe)
    except: 
        pass
    try:
        if pd.notna(lc) and float(lc) != 0:
            return float(lc), float(loc)
    except:
        pass
    return 0.0, 0.0

def _osrm_minutes(lat1, lon1, lat2, lon2, profile="driving"):
    url = f"https://router.project-osrm.org/route/v1/{profile}/{lon1},{lat1};{lon2},{lat2}"
    r = requests.get(url, params={"overview": "false"}, timeout=10)
    r.raise_for_status()
    data = r.json()
    sec = data["routes"][0]["duration"]
    return int(round(sec / 60))

def ajustar_horario_contra_final_osrm(df, idx_alvo, hora_final_fixa, lat_dest, lon_dest, buffer_min=3):
    """
    Ajusta SOMENTE o HORARIO do idx_alvo, calculando de trás pra frente:
      HORARIO_alvo = hora_final_fixa - duracao(OSRM do alvo -> proximo ponto) - buffer
    Regra do próximo ponto:
      - próximo na ORDEM dentro da mesma ROTA com GPS
      - se não existir, usa destino final (lat_dest, lon_dest)
    """
    df = df.copy()
    if idx_alvo not in df.index:
        return df, "IDX inválido"

    # hora final fixa
    dt_final = _parse_hhmm(hora_final_fixa)
    if dt_final is None:
        return df, "Hora final fixa inválida (ex: 06:37)"

    # rota / ordem do alvo
    rota = str(df.at[idx_alvo, 'ROTA']).strip().upper() if 'ROTA' in df.columns else ""
    if not rota:
        return df, "Sem ROTA no passageiro"

    ordem_alvo = int(pd.to_numeric(df.at[idx_alvo, 'ORDEM'], errors='coerce')) if 'ORDEM' in df.columns else None
    if ordem_alvo is None:
        return df, "Sem ORDEM no passageiro"

    # coords do alvo (embarque ou casa)
    latA, lonA = _get_best_latlon(df.loc[idx_alvo])
    if latA == 0:
        return df, "Passageiro sem GPS (casa/embarque)"

    # pega próximos da rota (ordem maior)
    dfr = df[df['ROTA'].astype(str).str.strip().str.upper() == rota].copy()
    dfr['ORDEM'] = pd.to_numeric(dfr['ORDEM'], errors='coerce').fillna(10**9)
    dfr = dfr.sort_values('ORDEM', kind='mergesort')

    # acha próximo com GPS
    latB, lonB = 0.0, 0.0
    achou = False
    for idx2, row2 in dfr.iterrows():
        if int(row2['ORDEM']) <= ordem_alvo:
            continue
        latB, lonB = _get_best_latlon(row2)
        if latB != 0:
            achou = True
            break

    # se não achou, usa destino final
    if not achou:
        if lat_dest == 0 or lon_dest == 0:
            return df, "Sem próximo ponto com GPS e sem DESTINO FINAL (lat/lon)"
        latB, lonB = float(lat_dest), float(lon_dest)

    # calcula OSRM e ajusta contra o final
    mins = _osrm_minutes(latA, lonA, latB, lonB)
    dt_novo = dt_final - timedelta(minutes=(mins + int(buffer_min)))

    df.at[idx_alvo, 'HORARIO'] = _fmt_hhmm(dt_novo)
    return df, f"OK: {df.at[idx_alvo,'HORARIO']} (OSRM {mins} min + buffer {buffer_min})"
import pandas as pd
import requests
from datetime import datetime, timedelta

def _parse_hora(v):
    s = str(v).strip().upper().replace('H', ':').replace('.', ':')
    if not s or 'X' in s or ':' not in s:
        return None
    p = s.split(':')
    try:
        return datetime(2000,1,1,int(p[0]),int(p[1]))
    except:
        return None

def _fmt_hora(dt):
    return dt.strftime("%H:%M")

def _coord(row):
    # embarque se existir, senão casa
    le = row.get('LATITUDE EMBARQUE', 0) or 0
    loe = row.get('LONGITUDE EMBARQUE', 0) or 0
    lc = row.get('LATITUDE CASA', 0) or 0
    loc = row.get('LONGITUDE CASA', 0) or 0
    try:
        le = float(le); loe = float(loe); lc = float(lc); loc = float(loc)
    except:
        return 0.0, 0.0
    if le != 0.0 and loe != 0.0: return le, loe
    if lc != 0.0 and loc != 0.0: return lc, loc
    return 0.0, 0.0

def _osrm_min(lat1, lon1, lat2, lon2, profile="driving"):
    url = f"https://router.project-osrm.org/route/v1/{profile}/{lon1},{lat1};{lon2},{lat2}"
    r = requests.get(url, params={"overview":"false"}, timeout=12)
    r.raise_for_status()
    sec = r.json()["routes"][0]["duration"]
    return int(round(sec/60))

def recalcular_horarios_cirurgico(df, rota_alvo, ordem_novo, buffer_min=2, modo_saida=False):
    """
    Recalcula apenas para TRÁS:
    se novo entrou na ordem_novo, recalcula ordem_novo-1 ... 1.
    NÃO mexe em ordem_novo, nem nas ordens maiores.
    ENTRADA: ignora XXXXX (não recalcula em cima deles)
    """
    df = df.copy()
    rota_alvo = str(rota_alvo).strip().upper()

    # seleciona rota
    mask = df['ROTA'].astype(str).str.strip().str.upper() == rota_alvo
    dfr = df[mask].copy()
    if dfr.empty:
        return df

    dfr['ORDEM_NUM'] = pd.to_numeric(dfr.get('ORDEM', 0), errors='coerce').fillna(10**9)
    dfr = dfr.sort_values('ORDEM_NUM', kind='mergesort')

    # pega “âncora” = primeiro ponto a partir da ordem_novo que tenha horário válido
    anchor_idx = None
    anchor_dt = None

    for idx, row in dfr.iterrows():
        o = int(row['ORDEM_NUM']) if row['ORDEM_NUM'] < 10**8 else None
        if o is None or o < int(ordem_novo):
            continue
        dt = _parse_hora(row.get('HORARIO', ''))
        if dt is not None:
            anchor_idx = idx
            anchor_dt = dt
            break

    if anchor_idx is None:
        # sem âncora -> não mexe
        return df

    # agora recalcula para trás: do (ordem do âncora - 1) até 1, mas só até ordem_novo-1
    # primeiro, monta lista ordenada até a âncora
    dfr_upto = dfr[dfr['ORDEM_NUM'] <= dfr.loc[anchor_idx, 'ORDEM_NUM']].copy()
    # cria mapa ordem -> idx
    ordem_to_idx = {}
    for idx, row in dfr_upto.iterrows():
        o = int(row['ORDEM_NUM'])
        ordem_to_idx[o] = idx

    # recalcula do min(anchor-1, ordem_novo-1) até 1
    o_anchor = int(dfr.loc[anchor_idx, 'ORDEM_NUM'])
    start = min(o_anchor - 1, int(ordem_novo) - 1)

    # garante que o anchor horário fique como está
    df.at[anchor_idx, 'HORARIO'] = _fmt_hora(anchor_dt)

    # itera para trás
    dt_next = anchor_dt
    idx_next = anchor_idx

    for o in range(start, 0, -1):
        if o not in ordem_to_idx:
            continue
        idx_cur = ordem_to_idx[o]

        # ENTRADA: se o ponto atual é XXXXX, não recalcula e não usa ele como base
        if (not modo_saida) and ('X' in str(df.at[idx_cur, 'HORARIO']).upper()):
            continue

        lat1, lon1 = _coord(df.loc[idx_cur])
        lat2, lon2 = _coord(df.loc[idx_next])
        if lat1 == 0.0 or lat2 == 0.0:
            # sem gps -> pula
            continue

        try:
            mins = _osrm_min(lat1, lon1, lat2, lon2)
        except:
            continue

        dt_cur = dt_next - timedelta(minutes=(mins + int(buffer_min)))
        df.at[idx_cur, 'HORARIO'] = _fmt_hora(dt_cur)

        # avança para trás
        dt_next = dt_cur
        idx_next = idx_cur

    return df
def atualizar_tracado_osrm_da_rota(df, nome_rota, lat_dest, lon_dest):
    nome_rota = str(nome_rota).strip().upper()
    dfr = df[df['ROTA'].astype(str).str.strip().str.upper() == nome_rota].copy()
    if dfr.empty:
        return False, "Rota vazia."

    # usa ORDEM para preservar a sequência atual
    if 'ORDEM' in dfr.columns:
        dfr['ORDEM_NUM'] = pd.to_numeric(dfr['ORDEM'], errors='coerce').fillna(10**9)
        dfr = dfr.sort_values('ORDEM_NUM', kind='mergesort')

    coords = []
    for _, row in dfr.iterrows():
        lat = row['LATITUDE EMBARQUE'] if row.get('LATITUDE EMBARQUE', 0) != 0 else row.get('LATITUDE CASA', 0)
        lon = row['LONGITUDE EMBARQUE'] if row.get('LONGITUDE EMBARQUE', 0) != 0 else row.get('LONGITUDE CASA', 0)
        if lat and lon:
            coords.append((float(lat), float(lon)))

    if len(coords) < 2:
        return False, "Poucos pontos com GPS para montar rota."

    coords.append((float(lat_dest), float(lon_dest)))

    geo_str, tempo_total_min, _ = get_rota_osrm(coords)
    if not geo_str:
        return False, "OSRM falhou (sem geometria)."

    salvar_trajeto_cache(nome_rota, geo_str, tempo_total_min)
    return True, f"Traçado OSRM atualizado. Tempo total ~ {tempo_total_min:.1f} min"
