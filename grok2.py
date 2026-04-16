import streamlit as st
import pandas as pd
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter
from geopy.distance import geodesic
import folium
from folium.plugins import BeautifyIcon, AntPath, Geocoder
from folium.features import DivIcon # <--- IMPORTANTE PARA O TEXTO NO MAPA
from streamlit_folium import st_folium
import json
import os
import time
from datetime import datetime
import re # <--- ADICIONE ISTO
from folium.plugins import MarkerCluster # <--- ADICIONE ISSO NO TOPO
import io
import requests
import numpy as np
import math

def streetview_url(lat, lon):
    return f"https://www.google.com/maps/@?api=1&map_action=pano&viewpoint={lat},{lon}"

# UNUSED — substituída por lógica inline
def processar_rota_novos(index_real_da_tabela):
    """
    index_real_da_tabela: lista com o index do df_novos na mesma ordem que aparece no editor
    """
    # O Streamlit guarda as mudanças do data_editor aqui
    changes = st.session_state.get("editor_novos", {})
    edited = changes.get("edited_rows", {})  # {row_pos: {"COL": valor, ...}}

    if not edited:
        return

    for row_pos, cols in edited.items():
        if "ROTA" not in cols:
            continue

        nova_rota = str(cols["ROTA"]).upper().strip()
        nova_rota = re.sub(r"\s+", " ", nova_rota)

        # ✅ Gate: só roda se a rota estiver "completa"
        if not re.match(r"^(VAN|MICRO|ONIBUS|ÔNIBUS)\s+\d{1,3}$", nova_rota):
            continue

        # mapeia a posição do editor -> índice real no seu df_ativo
        idx_real = index_real_da_tabela[int(row_pos)]

        rota_antiga = str(st.session_state["df_ativo"].at[idx_real, "ROTA"]).strip().upper()
        if nova_rota == rota_antiga:
            continue

        # aqui você coloca exatamente o que já faz hoje quando muda rota:
        st.session_state["df_ativo"].at[idx_real, "ROTA"] = nova_rota

        # (opcional) chama seu ímã/fallback aqui, igual já está no seu código
        # usou_ima, msg_ima = usar_ponto_existente_proximo(...)
        # ... etc

    # limpa as edições para não reprocessar no próximo rerun
    if "editor_novos" in st.session_state:
        st.session_state["editor_novos"]["edited_rows"] = {}
    
# IMPORTA O CÉREBRO
try:
    from otimizador import (
    OtimizadorRotas,
    usar_ponto_existente_proximo,
    buscar_gps_unico,
    reverse_geocode,
    obter_bairro_cidade,
    salvar_correcao_permanente,   # <--- NOVO
    aplicar_correcoes_memoria,     # <--- NOVO
    salvar_no_cache,
    inserir_e_otimizar_osrm,              # <-- ADD
    inserir_ponto_cirurgico_por_ordem,
    inserir_por_vizinho_adjacente,
    corrigir_duplicados_ordem,
    inserir_por_vizinho_geografico, 
    simular_rota,
    aplicar_so_vazios,
    aplicar_todos,
    renumerar_ordem_rota,
    
)
except ImportError as e:
    st.error(f"Erro ao importar: {e}")

# --- CONFIGURAÇÃO ---
st.set_page_config(page_title="Roteirizador V28 (Gerencial)", 
    layout="wide", 
    initial_sidebar_state="collapsed"  # <--- ADICIONE ISSO (Começa fechado)
)

from trial_guard import validar_ou_bloquear, get_status as _lic_status, machine_id as _machine_id
validar_ou_bloquear(st)

# ── Status de licença na sidebar ──────────────────────────────────────────────
def _mostrar_status_licenca():
    _s = _lic_status()
    with st.sidebar:
        with st.expander("🔑 Licença", expanded=False):
            mid = _s["machine_id"]
            if _s["ativo"]:
                dias = _s["dias_restantes"]
                tipo = "Trial" if _s["tipo"] == "trial" else "Licenciado"
                if dias <= 7:
                    st.warning(f"⚠️ {tipo} — expira em **{dias} dia(s)** ({_s['expira']})")
                else:
                    st.success(f"✅ {tipo} — **{dias} dias** restantes ({_s['expira']})")
            else:
                st.error("❌ Licença expirada")
            st.divider()
            st.caption("ID desta máquina (envie ao suporte):")
            st.code(mid, language=None)
            st.caption("Copie o código acima e mande por WhatsApp/e-mail para o suporte ativar sua licença.")

_mostrar_status_licenca()
# ─────────────────────────────────────────────────────────────────────────────

# --- CONFIGURAÇÃO DE TELA FIXA (DASHBOARD MODE) ---
st.markdown("""
    <style>
        /* 1. Tira o espaço em branco gigante do topo */
        .block-container {
            padding-top: 0  rem !important;
            padding-bottom: 0rem !important;
            padding-left: 1rem !important;
            padding-right: 1rem !important;
            max-width: 100% !important;
        }
        
        /* 2. Esconde Rodapé e Menu de 3 pontinhos (Opcional) */
        footer {visibility: hidden;}
        #MainMenu {visibility: hidden;} /* Esconde os 3 pontinhos */
        
        /* header {visibility: hidden;} */ /* <--- COMENTE OU APAGUE ESTA LINHA */
        /* Se deixar o header hidden, você perde o botão de abrir o menu! */

        /* 3. Barra de Rolagem (Estilo) */
        ::-webkit-scrollbar { width: 8px; height: 8px; }
        ::-webkit-scrollbar-track { background: #f1f1f1; }
        ::-webkit-scrollbar-thumb { background: #888; border-radius: 4px; }
        ::-webkit-scrollbar-thumb:hover { background: #555; }
    </style>
""", unsafe_allow_html=True)

FILE_CLIENTES = "clientes.json"
FILE_CACHE = "cache_enderecos.json"

# --- FUNÇÕES ---
# --- FUNÇÕES ATUALIZADAS ---
def calcular_sugestoes_rota(df, raio_max=1500):
    if 'SUGESTAO' not in df.columns: df['SUGESTAO'] = ""
    
    # 1. Mapa de Lotação (Conta passageiros atuais em cada rota)
    contagem_rotas = df['ROTA'].value_counts().to_dict()
    
    mask_tem_rota = ~df['ROTA'].astype(str).isin(['', 'nan', 'None', '0', '0.0'])
    df_base = df[mask_tem_rota & (df['LATITUDE EMBARQUE'] != 0)].copy()
    
    if df_base.empty: return df

    mask_sem_rota = df['ROTA'].astype(str).isin(['', 'nan', 'None', '0', '0.0'])
    df_alvos = df[mask_sem_rota & (df['LATITUDE CASA'] != 0)]

    for idx, row in df_alvos.iterrows():
        min_dist = float('inf')
        sugestao = ""
        
        for _, base in df_base.iterrows():
            d = geodesic((row['LATITUDE CASA'], row['LONGITUDE CASA']), 
                         (base['LATITUDE EMBARQUE'], base['LONGITUDE EMBARQUE'])).meters
            
            if d < raio_max and d < min_dist:
                min_dist = d
                r_nome = base['ROTA']
                
                # Inteligência de Capacidade
                qtd_atual = contagem_rotas.get(r_nome, 0)
                cap = 15
                if 'MICRO' in str(r_nome).upper(): cap = 28
                elif 'ONIBUS' in str(r_nome).upper(): cap = 46
                
                # Formata: "VAN 01 (300m) [14/15]"
                sugestao = f"{r_nome} ({int(d)}m) [{qtd_atual}/{cap}]"
        
        df.at[idx, 'SUGESTAO'] = sugestao

    return df
def recalcular_distancia_1_linha(df, idx):
    if idx not in df.index:
        return df

    import math

    # Local helper — escopo restrito a recalcular_distancia_1_linha.
    # Existe também uma versão global _to_float_ok mais abaixo (escopo diferente).
    def _to_float_ok(v):
        try:
            if v is None or (isinstance(v, float) and math.isnan(v)):
                return 0.0
            v = float(v)
            if not math.isfinite(v):
                return 0.0
            return v
        except:
            return 0.0

    lat_c = _to_float_ok(df.at[idx, 'LATITUDE CASA']) if 'LATITUDE CASA' in df.columns else 0.0
    lon_c = _to_float_ok(df.at[idx, 'LONGITUDE CASA']) if 'LONGITUDE CASA' in df.columns else 0.0
    lat_e = _to_float_ok(df.at[idx, 'LATITUDE EMBARQUE']) if 'LATITUDE EMBARQUE' in df.columns else 0.0
    lon_e = _to_float_ok(df.at[idx, 'LONGITUDE EMBARQUE']) if 'LONGITUDE EMBARQUE' in df.columns else 0.0

    if lat_c != 0.0 and lon_c != 0.0 and lat_e != 0.0 and lon_e != 0.0:
        try:
            df.at[idx, 'DIST_EMBARQUE_M'] = int(geodesic((lat_c, lon_c), (lat_e, lon_e)).meters)
        except:
            pass

    return df

# =========================================================
# FUNÇÕES DE HISTÓRICO E LOGS (COPIAR E COLAR NO TOPO)
# =========================================================
FILE_LOGS = "historico_logs.json"

def registrar_log(acao, detalhe, rota_afetada=None):
    """
    Grava um evento no arquivo de histórico (JSON).
    Ex: registrar_log("MUDANÇA ROTA", "João mudou para VAN 02", "VAN 02")
    """
    try:
        logs = []
        if os.path.exists(FILE_LOGS):
            with open(FILE_LOGS, "r", encoding="utf-8") as f:
                logs = json.load(f)
        
        novo_evento = {
            "data": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
            "acao": acao.upper(),
            "rota": str(rota_afetada) if rota_afetada else "-",
            "detalhe": detalhe
        }
        
        # Adiciona no topo e limita a 1000 registros
        logs.insert(0, novo_evento)
        if len(logs) > 1000: logs = logs[:1000]
        
        with open(FILE_LOGS, "w", encoding="utf-8") as f:
            json.dump(logs, f, ensure_ascii=False, indent=4)
            
    except Exception as e:
        print(f"Erro ao salvar log: {e}")

        # Coloque isso logo no início do arquivo, junto com as outras funções
def extrair_numero_rota(texto):
    import re
    # Procura o primeiro número no texto (Ex: "VAN 05" -> 5)
    match = re.search(r'(\d+)', str(texto))
    if match:
        return int(match.group(1))
    return 99999 # Se não tiver número, vai pro final

def get_rotas_alteradas_historico():
    """
    Lê o JSON de logs e retorna uma lista de todas as rotas que foram modificadas.
    Isso serve para saber quais rotas devemos recalcular o horário e quais mantemos o original.
    """
    rotas_mexidas = set()
    if os.path.exists(FILE_LOGS):
        try:
            with open(FILE_LOGS, "r", encoding="utf-8") as f:
                logs = json.load(f)
            
            for item in logs:
                # Se a ação for de edição ou troca, marca a rota como "mexida"
                acao = str(item.get('acao', '')).upper()
                if acao in ["EDICAO MAPA", "TROCA ROTA", "INSERCAO", "READEQUACAO"]:
                    r = str(item.get('rota', '')).strip().upper()
                    if r and r not in ['-', 'NONE', '']:
                        rotas_mexidas.add(r)
        except: pass
    return rotas_mexidas
def registrar_alteracao_rota(df, nome_rota):
    """
    Carimba a data atual na coluna DATA_ALTERACAO para todos daquela rota.
    Isso avisa a exportação que essa rota precisa ser recalculada.
    """
    if not nome_rota: return df
    
    agora = datetime.now().strftime("%d/%m/%Y %H:%M")
    
    # Se a coluna não existir, cria agora
    if 'DATA_ALTERACAO' not in df.columns:
        df['DATA_ALTERACAO'] = None
        
    mask = df['ROTA'] == nome_rota
    df.loc[mask, 'DATA_ALTERACAO'] = agora
    
    return df
def load_json(arquivo, default):
    if os.path.exists(arquivo):
        try:    
            with open(arquivo, "r", encoding="utf-8") as f: return json.load(f)
        except: return default
    return default

def save_json(arquivo, data):
    with open(arquivo, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=4)

def normalize_key(texto):
    if not isinstance(texto, str): return ""
    return texto.strip().lower().replace(', ', ',').replace(' - ', '-')

# UNUSED
def converter_coord(valor):
    try:
        if pd.isna(valor) or valor == "": return 0.0
        if isinstance(valor, str): valor = valor.replace(',', '.')
        return float(valor)
    except: return 0.0

# UNUSED
def get_iniciais(nome):
    if not isinstance(nome, str): return "??"
    partes = nome.strip().split()
    if len(partes) > 1: return (partes[0][0] + partes[1][0]).upper()
    return nome[:2].upper()

def get_cor_rota(nome_rota):
    if not nome_rota or pd.isna(nome_rota) or str(nome_rota) in ['nan', 'None', '']: return '#3388ff' 
    cores = ['#28a745', '#6f42c1', '#fd7e14', '#dc3545', '#17a2b8', '#e83e8c', '#20c997', '#343a40', '#6610f2', '#ffc107']
    idx = hash(str(nome_rota)) % len(cores)
    return cores[idx]

def get_estrutura_padrao():
    # Adicionamos TIPO_GEO para controlar a qualidade (1=Exato, 3=Aproximado)
    return pd.DataFrame(columns=[
        'SEL_CASA', 'SEL_EMB', 'SEL_DES', 'ORDEM', 'MATRICULA', 'NOME', 'HORARIO', 'ENDERECO', 'BAIRRO', 'CIDADE', 
        'LATITUDE CASA', 'LONGITUDE CASA', 'TIPO_GEO', 
        'EMBARQUE', 'LATITUDE EMBARQUE', 'LONGITUDE EMBARQUE', 'DIST_EMBARQUE_M',
        'ROTA', 'TURNO', 'DESEMBARQUE', 'LAT DES', 'LON DESEMBRQUE'
    ])

def scanner_metadados_excel(arquivo_excel, nome_destino_alvo):
    """
    Retorna metadados por ABA e por ROTA:
    {
      "Aba1": {"VAN 01": {"chegada": "21h40", "km": "12,3"}, ...},
      "Aba2": {"VAN 01": {"chegada": "06h37"}, ...}
    }
    """
    metadados = {}

    try:
        dfs = pd.read_excel(arquivo_excel, sheet_name=None, header=None)
        target = str(nome_destino_alvo).upper().strip()

        for nome_aba, df in dfs.items():
            ultimo_rota_visto = None  # ✅ CRÍTICO: reseta por aba
            metadados[nome_aba] = {}

            for r in range(len(df)):
                linha = df.iloc[r].values

                # 1) Descobre a rota atual
                for celula in linha:
                    txt = str(celula).upper().strip()
                    if ('VAN' in txt or 'MICRO' in txt or 'ONIBUS' in txt or 'ÔNIBUS' in txt) and len(txt) < 15:
                        if "PLACA" not in txt and "CAPACIDADE" not in txt:
                            ultimo_rota_visto = txt
                            if ultimo_rota_visto not in metadados[nome_aba]:
                                metadados[nome_aba][ultimo_rota_visto] = {}

                # 2) Regra de ouro: destino -> pega vizinho à direita
                if ultimo_rota_visto and len(target) > 2:
                    for c in range(len(linha) - 1):
                        celula_atual = str(linha[c]).upper().strip()
                        if target in celula_atual:
                            vizinho = str(linha[c + 1]).strip()

                            clean_viz = vizinho.lower().replace('h', ':').replace('.', ':')
                            if ':' in clean_viz and any(ch.isdigit() for ch in clean_viz):
                                metadados[nome_aba][ultimo_rota_visto]['chegada'] = clean_viz.replace(':', 'h')
                                break

                # 3) KM (mantido)
                if ultimo_rota_visto:
                    texto_linha = " ".join([str(x).upper() for x in linha])
                    if 'KM' in texto_linha:
                        import re
                        match = re.search(r'(?:KM)[:\s]*([\d,\.]+)', texto_linha)
                        if match:
                            metadados[nome_aba][ultimo_rota_visto]['km'] = match.group(1)

        return metadados

    except Exception as e:
        print(f"Erro Scanner: {e}")
        return {}

# NOTA: Como o layout é complexo, sugiro uma abordagem mais simples no Passo 3:
# Ler o horário da última linha de cada rota "virgem" diretamente do DataFrame carregado.

RAIO_UI_M = 1000          # o que o usuário vê
ESCALA_REAL = 0.85       # 1000 "vira" 700
def raio_real_m(raio_ui_m: float) -> float:
    return float(raio_ui_m) * ESCALA_REAL

def tratar_endereco_bruto(df):
    # Dicionário de correções
    mapa_cidades = {
        'CTBA': 'Curitiba', 
        'CURITIBA': 'Curitiba', 
        'C. LARGO': 'Campo Largo', 
        'CAMPO LARGO': 'Campo Largo',
        'S. J. PINHAIS': 'São José dos Pinhais', 
        'SJP': 'São José dos Pinhais',
        'ARAUC': 'Araucária',
        'ARAUCARIA': 'Araucária',
        'F.R.G': 'Fazenda Rio Grande',
        'F.R.G.': 'Fazenda Rio Grande',
        'FRG': 'Fazenda Rio Grande',
        'B.NOVA': 'Balsa Nova',
        'B. NOVA': 'Balsa Nova',
        'C.MAGRO': 'Campo Magro',
        'C. MAGRO': 'Campo Magro',
        'C.SANTANA': 'Campo do Santana',
        'CONTENDA': 'Contenda',
        'Alm. Tam': 'Almirante Tamandaré'
        
    }

    # Verifica se a coluna existe para não dar erro
    if 'CIDADE' in df.columns:
        # 1. Garante que tudo é texto
        df['CIDADE'] = df['CIDADE'].astype(str)
        
        # 2. Converte tudo para MAIÚSCULO e remove espaços das pontas (" CTBA " vira "CTBA")
        # Isso é crucial para o mapa funcionar
        df['CIDADE'] = df['CIDADE'].str.upper().str.strip()
        
        # 3. APLICA A SUBSTITUIÇÃO (Esta é a linha que faltava ou estava falhando)
        df['CIDADE'] = df['CIDADE'].replace(mapa_cidades)
        
        # 4. (Opcional) Deixa bonitinho (Primeira Letra Maiúscula: "Curitiba")
        df['CIDADE'] = df['CIDADE'].str.title()

    return df

# UNUSED — substituída por lógica inline
def inserir_novo_na_rota_unificado(df_all, idx_novo, rota_alvo, lat_dest, lon_dest, hora_alvo):
    df = df_all.copy()
    rota = str(rota_alvo).strip().upper()

    # garante rota
    if 'ROTA' in df.columns:
        df.at[idx_novo, 'ROTA'] = rota

    # 1) tenta ÍMÃ (ponto próximo)
    ret = usar_ponto_existente_proximo(df, idx_novo, rota)

    # compatibilidade: às vezes retorna 2, às vezes 3 valores
    ok = False
    idx_viz = None
    if isinstance(ret, tuple):
        if len(ret) == 3:
            ok, _msg, idx_viz = ret
        elif len(ret) == 2:
            ok, _msg = ret

    if ok and idx_viz is not None:
        # ✅ entra logo depois do vizinho (mesma lógica do "Um Só" atual)
        df = inserir_por_vizinho_adjacente(df, idx_novo, rota, idx_viz)
        df = corrigir_duplicados_ordem(df, rota)
        return df

    # 2) se não colou no ímã, usa CASA como embarque (seu fallback atual)
    df.at[idx_novo, 'EMBARQUE'] = df.at[idx_novo, 'ENDERECO']
    df.at[idx_novo, 'LATITUDE EMBARQUE'] = df.at[idx_novo, 'LATITUDE CASA']
    df.at[idx_novo, 'LONGITUDE EMBARQUE'] = df.at[idx_novo, 'LONGITUDE CASA']

    # 3) sem ímã => encaixe por "melhor posição" (OSRM)
    if lat_dest and lon_dest:
        df = inserir_e_otimizar_osrm(df, idx_novo, rota, lat_dest, lon_dest, hora_alvo)

    return df

def normalizar_df(df):
    """
    Versão Corrigida: Sem st.toast para evitar erro de Cache.
    """
    # 1. Padroniza Cabeçalhos
    df.columns = [str(c).strip().upper() for c in df.columns]
    
    # --- CORREÇÃO CIDADE ---
    if 'tratar_endereco_bruto' in globals():
        df = tratar_endereco_bruto(df)

    # 2. Mapeamento Inteligente
    mapa = {
        'ORDEM': 'ORDEM', 'POSICAO': 'ORDEM', 'SEQ': 'ORDEM',
        'MATRICULA': 'MATRICULA', 'MATRÍCULA': 'MATRICULA', 'RE': 'MATRICULA',
        'NOME': 'NOME', 'NOMES': 'NOME', 'PASSAGEIRO': 'NOME', 'COLABORADOR': 'NOME',
        'ENDEREÇO': 'ENDERECO', 'ENDERECO': 'ENDERECO', 'RUA': 'ENDERECO', 'RESIDENCIA': 'ENDERECO',
        'BAIRRO': 'BAIRRO',
        'CIDADE': 'CIDADE', 'MUNICÍPIO': 'CIDADE',
        'EMBARQUE': 'EMBARQUE', 'PONTO': 'EMBARQUE', 'LOCAL': 'EMBARQUE',
        'HORARIO': 'HORARIO', 'HORÁRIO': 'HORARIO', 'HORA': 'HORARIO',
        'ROTA': 'ROTA', 'LINHA': 'ROTA',
        'TURNO': 'TURNO'
    }
    df = df.rename(columns=mapa)

    if 'ORDEM' in df.columns:
        # Converte para número (buracos viram NaN)
        df['ORDEM'] = pd.to_numeric(df['ORDEM'], errors='coerce')
    else:
        # Se não existir, cria NaN (vai ser preenchido por rota)
        df['ORDEM'] = pd.NA

    # Garante ROTA existe (para reiniciar sequência por rota)
    if 'ROTA' not in df.columns:
        df['ROTA'] = ""

    # Normaliza texto da rota (evita "van 01" ≠ "VAN 01")
    df['ROTA'] = df['ROTA'].astype(str).str.strip().str.upper()

    # Preenche buracos e reseta sequência 1..N dentro de cada rota
    def _preencher_e_resetar_ordem(gr):
        gr = gr.copy()

        # Preenche apenas os vazios com sequência após o maior existente
        valid = gr['ORDEM'].dropna()
        max_atual = int(valid.max()) if len(valid) else 0

        mask_na = gr['ORDEM'].isna()
        if mask_na.any():
            qtd = int(mask_na.sum())
            gr.loc[mask_na, 'ORDEM'] = list(range(max_atual + 1, max_atual + 1 + qtd))

        # Reset final 1..N
        gr = gr.sort_values('ORDEM')
        gr['ORDEM'] = range(1, len(gr) + 1)
        return gr

    df = df.groupby('ROTA', dropna=False, group_keys=False).apply(_preencher_e_resetar_ordem)

    # ORDEM final sempre int
    df['ORDEM'] = df['ORDEM'].astype(int)

    
    # 3. Garante Colunas Essenciais
    cols_essenciais = ['MATRICULA', 'NOME', 'ENDERECO', 'BAIRRO', 'CIDADE', 'EMBARQUE', 'HORARIO', 'ROTA', 'TURNO']
    for col in cols_essenciais:
        if col not in df.columns: df[col] = "" 
        else: df[col] = df[col].astype(str).replace(['nan', 'None', 'NaN', '0.0'], '')

    # --- 4. APLICAÇÃO DA REGRA DE OURO (FILTRO POR NOME) ---
    df = df[df['NOME'].str.strip() != '']
    df = df[df['NOME'].str.lower() != 'nan']
    
    termos_proibidos = ['VAN ','VAN', 'VAN-', 'MICO', 'MICRO', 'ONIBUS', 'ÔNIBUS', 'PLACA', 'CAPACIDADE', 'TOTAL','FUNCIONÁRIO']
    def eh_veiculo(texto):
        t = str(texto).upper()
        for termo in termos_proibidos:
            if termo in t: return True
        return False
    
    df = df[~df['NOME'].apply(eh_veiculo)]
    
    # 5. Tratamento de Horário
    def limpar_hora(val):
        t = str(val).lower().strip()
        if 'x' in t: return "XXXXX" 
        t = t.replace('h', ':').replace(' ', '').replace('.', ':')
        if len(t) == 4 and t.isdigit(): return f"{t[:2]}:{t[2:]}" 
        return t.upper()
    df['HORARIO'] = df['HORARIO'].apply(limpar_hora)

    # 6. Tratamento de Coordenadas
    cols_gps = ['LATITUDE CASA', 'LONGITUDE CASA', 'LATITUDE EMBARQUE', 'LONGITUDE EMBARQUE', 'LAT DES', 'LON DESEMBRQUE']
    for c in cols_gps:
        if c not in df.columns: df[c] = 0.0
        else: df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0.0)

    # 7. Colunas de Controle
    if 'DIST_EMBARQUE_M' not in df.columns: df['DIST_EMBARQUE_M'] = 0
    if 'TIPO_GEO' not in df.columns: df['TIPO_GEO'] = 0
    for c in ['SEL_CASA', 'SEL_EMB', 'SEL_DES']: df[c] = False

    

    def extrair_tipo(texto_rota):
            t = str(texto_rota).upper()
            if 'VAN' in t: return 'VAN'
            if 'MICRO' in t: return 'MICRO'
            if 'ONIBUS' in t or 'ÔNIBUS' in t: return 'ONIBUS'
            return '' 
    df['VEICULO'] = df['ROTA'].apply(extrair_tipo)

    # Limpeza de Rota
    import re
    def limpar_espacos_extras(texto):
        if not isinstance(texto, str): return texto
        return re.sub(r'\s+', ' ', texto).strip().upper()
    if 'ROTA' in df.columns: df['ROTA'] = df['ROTA'].apply(limpar_espacos_extras)

    # ==============================================================================
    #  AUTO-CARREGAMENTO DO CACHE TÉCNICO
    # ==============================================================================
    try:
        if os.path.exists(FILE_CACHE):
            with open(FILE_CACHE, "r", encoding="utf-8") as f:
                cache = json.load(f)
            
            for i, row in df.iterrows():
                # 1. Recupera CASA
                end_casa = str(row.get('ENDERECO', ''))
                key_casa = end_casa.strip().lower().replace(', ', ',').replace(' - ', '-')
                
                if row.get('LATITUDE CASA', 0) == 0 and key_casa in cache:
                    dados = cache[key_casa]
                    df.at[i, 'LATITUDE CASA'] = dados['lat']
                    df.at[i, 'LONGITUDE CASA'] = dados['lon']
                    df.at[i, 'TIPO_GEO'] = dados.get('tipo', 1)
                    if 'nome' in dados and dados['nome']: df.at[i, 'ENDERECO'] = dados['nome']

                # 2. Recupera EMBARQUE
                end_emb = str(row.get('EMBARQUE', ''))
                key_emb = end_emb.strip().lower().replace(', ', ',').replace(' - ', '-')
                
                if row.get('LATITUDE EMBARQUE', 0) == 0 and key_emb in cache:
                    dados = cache[key_emb]
                    df.at[i, 'LATITUDE EMBARQUE'] = dados['lat']
                    df.at[i, 'LONGITUDE EMBARQUE'] = dados['lon']
                    if 'nome' in dados and dados['nome']: df.at[i, 'EMBARQUE'] = dados['nome']
    except Exception as e: print(f"Erro no auto-load cache: {e}")

    # ==============================================================================
    #  APLICA A MEMÓRIA PERMANENTE (CORREÇÕES MANUAIS)
    # ==============================================================================
    try:
        from otimizador import aplicar_correcoes_memoria
        df = aplicar_correcoes_memoria(df)
    except ImportError: pass 

# 🟡 (PASSO 2) COLE ISTO DENTRO DA FUNÇÃO normalizar_df (ANTES DO RETURN)
    if 'DATA_ALTERACAO' not in df.columns: 
        df['DATA_ALTERACAO'] = None 
    else:
        df['DATA_ALTERACAO'] = df['DATA_ALTERACAO'].replace({np.nan: None, 'nan': None})
    return df.reset_index(drop=True)


def aprender_novo_endereco(texto, lat, lon):
    if len(str(texto)) > 5:
        cache = load_json(FILE_CACHE, {})
        chave = normalize_key(texto)
        if chave not in cache:
            # Salva como tipo 1 (Manual/Confiável)
            cache[chave] = {'lat': lat, 'lon': lon, 'tipo': 1}
            save_json(FILE_CACHE, cache)

# NOVA FUNÇÃO DE BUSCA INTELIGENTE (CASCATA)
def buscar_gps_automatico(df_input):
    lat_fabrica = st.session_state.get('lat_dest', 0)
    lon_fabrica = st.session_state.get('lon_dest', 0)
    df = df_input.copy()
    
    if 'STATUS' not in df.columns: df['STATUS'] = 'OK'
    if 'BAIRRO' not in df.columns: df['BAIRRO'] = ''
    if 'CIDADE' not in df.columns: df['CIDADE'] = 'Curitiba'

    barra = st.sidebar.progress(0)
    total = len(df)
    alterados = 0
    if total == 0: barra.empty(); return df, 0
    
    vazios = ['', 'nan', 'None', '0', 'NaN']

    # LISTA DE CIDADES "DORMITÓRIO" (Onde a van não entra para pegar passageiro)
    # Se o funcionário for daqui, buscaremos o EMBARQUE em CURITIBA.
    CIDADES_FORA_AREA = [
        'FAZENDA RIO GRANDE', 'FRG', 'RIO GRANDE', 
        'MANDIRITUBA', 
        'QUITANDINHA', 
        'TIJUCAS DO SUL', 'TIJUCAS',
        'AGUDOS DO SUL', 'AGUDOS',
        'LAPA', 
        'CONTENDA',
        'BALSA NOVA'
    ]

    def extrair_lat_lon(texto):
        import re
        texto = str(texto)
        match = re.search(r'\(\s*(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)\s*\)', texto)
        if match: return float(match.group(1)), float(match.group(2))
        try:
            p = texto.replace('(', '').replace(')', '').replace(' ', '').split(',')
            if len(p) == 2: return float(p[0]), float(p[1])
        except: pass
        return 0.0, 0.0

    for i, row in df.iterrows():
        progresso = min((i + 1) / total, 1.0)
        barra.progress(progresso)
        
        cidade_atual = str(row.get('CIDADE', 'Curitiba'))
        if cidade_atual.strip() == '': cidade_atual = 'Curitiba'
        
        # --- LÓGICA INTELIGENTE DE FRONTEIRA ---
        eh_cidade_restrita = any(restrito in cidade_atual.upper() for restrito in CIDADES_FORA_AREA)
        # Se for restrita, força Curitiba. Se não, usa a cidade dele mesmo.
        cidade_para_embarque = "Curitiba" if eh_cidade_restrita else cidade_atual

        # =========================================================
        # 1. CASA (Busca na cidade REAL dele - ex: Fazenda)
        # =========================================================
        novo_lat, novo_lon = 0, 0
        end_casa = str(row.get('ENDERECO', ''))

        if row['LATITUDE CASA'] == 0:
            lat_dir, lon_dir = extrair_lat_lon(end_casa)
            if lat_dir != 0:
                novo_lat, novo_lon = lat_dir, lon_dir
                df.at[i, 'TIPO_GEO'] = 1
            elif len(end_casa) > 3:
                # Casa: Usa a cidade_atual (onde ele mora de verdade)
                lat, lon, tipo = buscar_gps_unico(end_casa, "", cidade_atual)
                if lat != 0:
                    novo_lat, novo_lon = lat, lon
                    df.at[i, 'TIPO_GEO'] = tipo

        if novo_lat != 0:
            df.at[i, 'LATITUDE CASA'] = novo_lat
            df.at[i, 'LONGITUDE CASA'] = novo_lon
            df.at[i, 'STATUS'] = 'OK'
            alterados += 1

            # Preenche Bairro/Cidade se faltar
            bairro_atual = str(row.get('BAIRRO', '')).strip()
            cid_atual_tab = str(row.get('CIDADE', '')).strip()
            if bairro_atual in vazios or cid_atual_tab in vazios:
                nb, nc = obter_bairro_cidade(novo_lat, novo_lon)
                if bairro_atual in vazios and nb: df.at[i, 'BAIRRO'] = nb
                if cid_atual_tab in vazios and nc: df.at[i, 'CIDADE'] = nc
            
            # Alerta de distância (visual apenas)
            if lat_fabrica != 0 and geodesic((novo_lat, novo_lon), (lat_fabrica, lon_fabrica)).km > 120:
                 df.at[i, 'STATUS'] = 'Erro: Distante (>120km)'

        # =========================================================
        # 2. EMBARQUE (Busca em CURITIBA se ele for de fora)
        # =========================================================
        lat_emb = row.get('LATITUDE EMBARQUE', 0)
        txt_emb = str(row.get('EMBARQUE', ''))
        novo_lat_emb, novo_lon_emb = 0, 0
        
        if (lat_emb == 0 or pd.isna(lat_emb)) and len(txt_emb) > 3:
             lat_dir, lon_dir = extrair_lat_lon(txt_emb)
             if lat_dir != 0:
                 novo_lat_emb, novo_lon_emb = lat_dir, lon_dir
                 # Se for coordenada, tenta descobrir o nome da rua (opcional)
                 df.at[i, 'EMBARQUE'] = reverse_geocode(lat_dir, lon_dir, usar_overpass=False) 
             else:
                 # AQUI USAMOS A CIDADE CORRIGIDA
                 lat_e, lon_e, _ = buscar_gps_unico(txt_emb, "", cidade_para_embarque, is_pickup=True)
                 if lat_e != 0:
                     novo_lat_emb, novo_lon_emb = lat_e, lon_e

             if novo_lat_emb != 0:
                 df.at[i, 'LATITUDE EMBARQUE'] = novo_lat_emb
                 df.at[i, 'LONGITUDE EMBARQUE'] = novo_lon_emb
                 if df.at[i, 'STATUS'] != 'Erro: Distante (>120km)': df.at[i, 'STATUS'] = 'OK'
                 alterados += 1

    save_json(FILE_CACHE, load_json(FILE_CACHE, {})) 
    barra.empty()
    return df, alterados
# --- STATE ---
if 'df_ativo' not in st.session_state: st.session_state['df_ativo'] = get_estrutura_padrao()
if 'ponto_provisorio' not in st.session_state: st.session_state['ponto_provisorio'] = None
if 'edit_target' not in st.session_state: st.session_state['edit_target'] = None
if 'sugestoes_agrupamento' not in st.session_state: st.session_state['sugestoes_agrupamento'] = []
if 'vizinhos_raio' not in st.session_state: st.session_state['vizinhos_raio'] = []
if 'show_add_cli' not in st.session_state: st.session_state['show_add_cli'] = False
if 'resultado_rotas' not in st.session_state: st.session_state['resultado_rotas'] = None

# =========================================================
# =========================================================
#               BARRA LATERAL (CÓDIGO COMPLETO)
# =========================================================
st.sidebar.title("🚍 Roteirizador")

# --- 🛡️ BLINDAGEM TOTAL (EVITA O ERRO DE KEYERROR) ---
# Se o dataframe não existir, cria um vazio
if 'df_ativo' not in st.session_state or st.session_state['df_ativo'] is None:
    st.session_state['df_ativo'] = pd.DataFrame()

# Garante que TODAS as colunas essenciais existam antes de qualquer conta
colunas_obrigatorias = {
    'ROTA': "",
    'NOME': "",
    'ORDEM': 0,
    'LATITUDE CASA': 0.0,
    'LONGITUDE CASA': 0.0,
    'TIPO_GEO': 0,
    'EMBARQUE': "",
    'HORARIO': "",
    'ENDERECO': "",
    'DIST_EMBARQUE_M': 0
}

for col, valor_padrao in colunas_obrigatorias.items():
    if col not in st.session_state['df_ativo'].columns:
        st.session_state['df_ativo'][col] = valor_padrao

        # ✅ GARANTIR ORDEM (tipo e valores)
st.session_state['df_ativo']['ORDEM'] = pd.to_numeric(
    st.session_state['df_ativo']['ORDEM'], errors='coerce'
).fillna(0).astype(int)
# -----------------------------------------------------

# --- 1. FILTROS ---
st.sidebar.markdown("### 🔍 Filtros")
busca = st.sidebar.text_input("Nome ou Endereço:", placeholder="Digite para buscar...")

# Filtro de Rotas (Seguro)
lista_rotas = st.session_state['df_ativo']['ROTA'].unique()
rotas_db = sorted([str(r) for r in lista_rotas if str(r) not in ['nan', 'None', '', '0', '0.0']])
opcoes_rotas = ["Novos (Sem Rota)"] + rotas_db 
#filtro_rotas_bar = st.sidebar.multiselect("Filtrar Rotas:", options=opcoes_rotas)

st.sidebar.markdown("---")


# --- 3. STATUS DO GPS (COM CÁLCULO DE DISTÂNCIA AUTOMÁTICO) ---
st.sidebar.markdown("### 📡 Status GPS")
df_temp = st.session_state['df_ativo']

# Conta quantos faltam
sem_gps = len(df_temp[(df_temp['LATITUDE CASA'] == 0)])
aprox_gps = len(df_temp[df_temp['TIPO_GEO'] == 3])

# Funçãozinha rápida para recalcular distâncias após achar o GPS
# Versão global de _to_float_ok (existe também versão local em recalcular_distancia_1_linha).


def _to_float_ok(v):
    try:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return 0.0
        v = float(v)
        if not math.isfinite(v):
            return 0.0
        return v
    except:
        return 0.0

def recalcular_distancias_pos_gps(df):
    for i, row in df.iterrows():
        lat_c = _to_float_ok(row.get('LATITUDE CASA', 0))
        lon_c = _to_float_ok(row.get('LONGITUDE CASA', 0))
        lat_e = _to_float_ok(row.get('LATITUDE EMBARQUE', 0))
        lon_e = _to_float_ok(row.get('LONGITUDE EMBARQUE', 0))

        # só calcula se tiver os 4 números ok
        if lat_c != 0.0 and lon_c != 0.0 and lat_e != 0.0 and lon_e != 0.0:
            try:
                df.at[i, 'DIST_EMBARQUE_M'] = int(geodesic((lat_c, lon_c), (lat_e, lon_e)).meters)
            except:
                pass
    return df


if sem_gps > 0:
    st.sidebar.error(f"🔴 {sem_gps} Sem Localização")
    if st.sidebar.button("🌍 Buscar GPS", use_container_width=True, type="primary"):
        with st.status("Geocodificando e Calculando Distâncias...", expanded=True):
            # 1. Busca o GPS
            novo_df, qtd = buscar_gps_automatico(st.session_state['df_ativo'])
            
            # 2. AGORA SIM: Recalcula as distâncias com os novos GPS encontrados
            novo_df = recalcular_distancias_pos_gps(novo_df)
            
            st.session_state['df_ativo'] = novo_df
            st.success("Concluído! Distâncias atualizadas.")
            time.sleep(1)
            st.rerun()

elif aprox_gps > 0:
    st.sidebar.warning(f"🟠 {aprox_gps} Aproximados (Só Rua)")
    if st.sidebar.button("🌍 Refinar Busca",  width="stretch"):#use_container_width=True):
         novo_df, qtd = buscar_gps_automatico(st.session_state['df_ativo'])
         st.session_state['df_ativo'] = recalcular_distancias_pos_gps(novo_df)
         st.rerun()
else:
    st.sidebar.success("✅ GPS 100% Ok")

st.sidebar.markdown("---")

# --- 3. DESTINO ---
st.sidebar.markdown("### 🏭 Destino")
clientes = load_json(FILE_CLIENTES, {})
c_dest1, c_dest2 = st.sidebar.columns([4, 1])
with c_dest1:
    cli_sel = st.selectbox("Selecione:", ["-- Selecione --"] + list(clientes.keys()), label_visibility="collapsed")
with c_dest2:
    if st.button("➕"): st.session_state['show_add_cli'] = not st.session_state['show_add_cli']

if st.session_state['show_add_cli']:
    with st.sidebar.form("add_c"):
        novo_nome = st.text_input("Nome")
        novo_end = st.text_input("Endereço")
        if st.form_submit_button("Salvar"):
            lat, lon, _ = buscar_gps_unico(novo_end)
            if lat != 0:
                clientes[novo_nome] = {'lat': lat, 'lon': lon, 'endereco': novo_end}
                save_json(FILE_CLIENTES, clientes)
                st.rerun()

# Define lat_dest
lat_dest, lon_dest = (clientes[cli_sel]['lat'], clientes[cli_sel]['lon']) if cli_sel != "-- Selecione --" else (0.0, 0.0)
    
# ✅ Persistir destino para o resto do app (OSRM, inserir ponto, etc.)
st.session_state["lat_dest"] = float(lat_dest or 0.0)
st.session_state["lon_dest"] = float(lon_dest or 0.0)
st.session_state["destino_nome"] = cli_sel


st.sidebar.markdown("---")

# --- 4. GESTÃO DE PASSAGEIROS ---
with st.sidebar.expander("➕ Adicionar Passageiros", expanded=False):
    tab_um, tab_lote = st.tabs(["👤 Um Só", "📂 Excel"])

    with tab_um:
        with st.form("add_single", clear_on_submit=True):
            nome_input = st.text_input("Nome")
            end_input = st.text_input("Endereço (Rua e Número)")
            cid_input = st.text_input("Cidade", value="")
            emb_input = st.text_input("Ponto Encontro (Opcional)")

            rota_alvo = st.text_input("ROTA destino (ex: VAN 01)")
            hora_chegada_target = st.text_input("Hora chegada na fábrica (ex: 06:37)", value="06:37")

            if st.form_submit_button("Adicionar"):
                if nome_input and end_input:
                    lat, lon, tipo = buscar_gps_unico(end_input, "", cid_input)

                    novo_p = {col: None for col in st.session_state['df_ativo'].columns}
                    novo_p['NOME'] = nome_input
                    novo_p['ENDERECO'] = end_input
                    novo_p['CIDADE'] = cid_input
                    novo_p['LATITUDE CASA'] = lat
                    novo_p['LONGITUDE CASA'] = lon
                    novo_p['TIPO_GEO'] = tipo

                    # Defaults
                    novo_p['EMBARQUE'] = ""
                    novo_p['LATITUDE EMBARQUE'] = 0.0
                    novo_p['LONGITUDE EMBARQUE'] = 0.0

                    # Se usuário informar ponto de encontro
                    if emb_input:
                        novo_p['EMBARQUE'] = emb_input
                        la_e, lo_e, _ = buscar_gps_unico(emb_input, is_pickup=True)
                        novo_p['LATITUDE EMBARQUE'] = la_e
                        novo_p['LONGITUDE EMBARQUE'] = lo_e

                    # Garante que não quebra
                    for c in ['LAT DES', 'LON DESEMBRQUE']:
                        if c in st.session_state['df_ativo'].columns:
                            novo_p[c] = 0.0
                    for c in ['SEL_CASA', 'SEL_EMB', 'SEL_DES']:
                        if c in st.session_state['df_ativo'].columns:
                            novo_p[c] = False

                    # Define ROTA
                    if 'ROTA' in st.session_state['df_ativo'].columns:
                        novo_p['ROTA'] = str(rota_alvo).strip().upper()

                    # ORDEM temporária (só pra não ficar NaN)
                    # if 'ORDEM' in st.session_state['df_ativo'].columns:
                    #     ord_max = pd.to_numeric(st.session_state['df_ativo']['ORDEM'], errors='coerce').max()
                    #     ord_max = int(ord_max) if pd.notna(ord_max) else 0
                    #     novo_p['ORDEM'] = ord_max + 1

                    # 1) adiciona no DF
                    st.session_state['df_ativo'] = pd.concat(
                        [st.session_state['df_ativo'], pd.DataFrame([novo_p])],
                        ignore_index=True
                    )

                    # ------------------------------------------------------------
                    # DEBUG + pós-processo do novato (encaixe ORDEM)
                    # ------------------------------------------------------------
                    df_all = st.session_state['df_ativo']
                    idx_novo = df_all.index.max()

                    rota_alvo = str(novo_p.get('ROTA', '')).strip().upper()

                    if rota_alvo and rota_alvo not in ['NAN', 'NONE', '0', '']:
                        df_all.at[idx_novo, 'ROTA'] = rota_alvo

                        # ===== DEBUG INÍCIO =====
                        print("\n" + "="*80)
                        print("[ADD] idx_novo =", idx_novo, "| rota_alvo =", rota_alvo)
                        print("[ADD] novo nome =", df_all.at[idx_novo, 'NOME'])
                        print("[ADD] ORDEM (antes) =", df_all.at[idx_novo, 'ORDEM'] if 'ORDEM' in df_all.columns else None)
                        print("[ADD] HORARIO (antes) =", df_all.at[idx_novo, 'HORARIO'] if 'HORARIO' in df_all.columns else None)
                        print("[ADD] coords CASA =", df_all.at[idx_novo, 'LATITUDE CASA'], df_all.at[idx_novo, 'LONGITUDE CASA'])
                        print("[ADD] coords EMB  =", df_all.at[idx_novo, 'LATITUDE EMBARQUE'], df_all.at[idx_novo, 'LONGITUDE EMBARQUE'])
                        # ===== DEBUG INÍCIO =====

                        # 1) tenta copiar embarque próximo (ímã)
                        ok, msg, idx_viz = usar_ponto_existente_proximo(df_all, idx_novo, rota_alvo)


                        # ===== DEBUG IMÃ =====
                        print("[IMÃ] ok =", ok, "| msg =", msg)
                        print("[IMÃ] EMBARQUE =", df_all.at[idx_novo, 'EMBARQUE'])
                        print("[IMÃ] HORARIO (depois) =", df_all.at[idx_novo, 'HORARIO'] if 'HORARIO' in df_all.columns else None)
                        print("[IMÃ] ORDEM (depois) =", df_all.at[idx_novo, 'ORDEM'] if 'ORDEM' in df_all.columns else None)
                        if ok and idx_viz is not None:
                            df_all = inserir_por_vizinho_adjacente(df_all, idx_novo, rota_alvo, idx_viz)
                            df_all = corrigir_duplicados_ordem(df_all, rota_alvo)


                            print("[COPIA] vizinho idx =", idx_viz,
                                "| nome =", df_all.at[idx_viz, 'NOME'],
                                "| ordem_viz =", df_all.at[idx_viz, 'ORDEM'],
                                "| ordem_novo =", df_all.at[idx_novo, 'ORDEM'])
                        st.session_state["df_ativo"] = df_all
                        # 2) se NÃO achou embarque, usa CASA como embarque
                        if not ok:
                            df_all.at[idx_novo, 'EMBARQUE'] = df_all.at[idx_novo, 'ENDERECO']
                            df_all.at[idx_novo, 'LATITUDE EMBARQUE'] = df_all.at[idx_novo, 'LATITUDE CASA']
                            df_all.at[idx_novo, 'LONGITUDE EMBARQUE'] = df_all.at[idx_novo, 'LONGITUDE CASA']

                            print("[FALLBACK] Sem ponto próximo -> EMBARQUE=casa")
                            print("[FALLBACK] coords EMB =", df_all.at[idx_novo, 'LATITUDE EMBARQUE'], df_all.at[idx_novo, 'LONGITUDE EMBARQUE'])

                            # ✅ 1) marca como "NOVO" na ORDEM
                            if 'ORDEM' in df_all.columns:
                                df_all.at[idx_novo, 'ORDEM'] = 999
                            else:
                                df_all['ORDEM'] = 0
                                df_all.at[idx_novo, 'ORDEM'] = 999

                            # ✅ 2) calcula posição sugerida (perto de quem) SEM renumerar a rota
                            # cria coluna de sugestão se não existir
                            if 'ORDEM_SUG' not in df_all.columns:
                                df_all['ORDEM_SUG'] = np.nan

                            try:
                                # usa sua função existente só para descobrir o vizinho e o lado
                                _tmp, idx_viz_geo, msg_ins = inserir_por_vizinho_geografico(
                                    df_all.copy(), idx_novo, rota_alvo, lat_dest, lon_dest
                                )
                                print("[SUGESTAO] inserir_por_vizinho_geografico:", msg_ins)

                                if idx_viz_geo is not None and idx_viz_geo in df_all.index:
                                    ord_viz = pd.to_numeric(df_all.at[idx_viz_geo, 'ORDEM'], errors='coerce')
                                    if pd.isna(ord_viz):
                                        ord_viz = 0
                                    ord_viz = int(ord_viz)

                                    # msg_ins vem com "lado=ANTES/DEPOIS" (sua função já faz isso)
                                    lado = "DEPOIS" if "lado=DEPOIS" in str(msg_ins).upper() else "ANTES"

                                    # regra simples: se é ANTES -> sugerir a ordem do vizinho
                                    # se é DEPOIS -> sugerir a ordem do vizinho + 1
                                    sugestao = ord_viz if lado == "ANTES" else (ord_viz + 1)

                                    df_all.at[idx_novo, 'ORDEM_SUG'] = sugestao
                                    df_all.at[idx_novo, 'VIZINHO_SUG'] = str(df_all.at[idx_viz_geo, 'NOME']) if 'VIZINHO_SUG' in df_all.columns else None

                                    print(f"[SUGESTAO] Novo perto de idx={idx_viz_geo} (ord={ord_viz}) -> ORDEM_SUG={sugestao} | ORDEM fica 999")
                                else:
                                    print("[SUGESTAO] Não achei vizinho geográfico para sugerir posição.")
                            except Exception as e:
                                print("[SUGESTAO] erro ao sugerir posição:", e)

                            # 🔴 IMPORTANTE: NÃO renumerar e NÃO corrigir duplicados aqui (você pediu para não mexer na ORDEM)

    with tab_lote:
        st.caption("Excel extra")
        upl_extra = st.file_uploader("Arquivo Extra", type=["xlsx"], key="upload_extra")
        if upl_extra:
            if st.button("Processar Upload"):
                try:
                    df_novo = pd.read_excel(upl_extra)
                    df_novo = normalizar_df(df_novo)

                    # =========================================================
                    # ✅ IGUAL "UM SÓ": tudo entra como NOVO (sem rota)
                    # =========================================================
                    if "ROTA" not in df_novo.columns:
                        df_novo["ROTA"] = ""
                    df_novo["ROTA"] = ""  # força ir para aba "Novos (Sem Rota)"

                    # opcional: deixa limpo como "Um Só"
                    for c in ["HORARIO", "EMBARQUE", "TURNO"]:
                        if c not in df_novo.columns:
                            df_novo[c] = ""
                    df_novo["HORARIO"] = ""  # novos não devem vir com horário pronto

                    # garante colunas de embarque (igual um só quando não informa ponto)
                    for c in ["LATITUDE EMBARQUE", "LONGITUDE EMBARQUE"]:
                        if c not in df_novo.columns:
                            df_novo[c] = 0.0

                    # ORDEM pode ficar (não atrapalha triagem), mas se quiser zerar:
                    if "ORDEM" in df_novo.columns:
                        df_novo["ORDEM"] = pd.to_numeric(df_novo["ORDEM"], errors="coerce").fillna(0).astype(int)

                    # =========================================================
                    # adiciona no df principal
                    # =========================================================
                    st.session_state["df_ativo"] = pd.concat(
                        [st.session_state["df_ativo"], df_novo],
                        ignore_index=True
                    )

                    # ✅ (recomendado) já gera sugestão pra aparecer na aba Novos
                    st.session_state["df_ativo"] = calcular_sugestoes_rota(st.session_state["df_ativo"])

                    st.success(f"{len(df_novo)} importados em NOVOS (sem rota)!")
                    st.rerun()

                except Exception as e:
                    st.error(f"Erro: {e}")


# --- 5. GESTÃO DE ARQUIVO E ABAS (INTELIGENTE) ---
# --- 5. GESTÃO DE ARQUIVO E ABAS (CORRIGIDO) ---
with st.sidebar.expander("📂 Arquivo e Abas", expanded=True):
    # 1. Upload do Arquivo
    uploaded_file = st.file_uploader("Subir Excel Base", type=["xlsx"], label_visibility="collapsed")
    header_row = st.number_input("Linha Cabeçalho", value=0, min_value=0)

    # Se subiu arquivo novo
    if uploaded_file and 'arquivo_carregado_id' not in st.session_state:
            try:
                # -----------------------------------------------------------
                # 1. RODA O SCANNER (AGORA COM O ARGUMENTO FALTANTE)
                # -----------------------------------------------------------
                uploaded_file.seek(0) 
                
                # Pega o nome do destino selecionado no menu acima (ou vazio se não tiver)
                nome_destino = str(cli_sel) if 'cli_sel' in locals() and cli_sel != "-- Selecione --" else ""
                
                # AQUI ESTAVA O ERRO: Agora passamos os dois argumentos
                mapa_kms_encontrados = scanner_metadados_excel(uploaded_file, nome_destino)
                
                st.session_state['mapa_km_cache'] = mapa_kms_encontrados 
                
                if mapa_kms_encontrados:
                    st.toast(f"Scanner leu sua planilha!", icon="✅")
                
                # -----------------------------------------------------------
                # 2. CARREGA O ARQUIVO NORMALMENTE
                # -----------------------------------------------------------
                uploaded_file.seek(0) 
                dict_abas = pd.read_excel(uploaded_file, sheet_name=None, header=header_row)
                st.session_state['todas_abas_backup'] = dict_abas
                
                primeira_aba = list(dict_abas.keys())[0]
                st.session_state['aba_atual'] = primeira_aba
                st.session_state['df_ativo'] = normalizar_df(dict_abas[primeira_aba])
                
                st.session_state['arquivo_carregado_id'] = uploaded_file.name
                st.rerun()
                
            except Exception as e:
                st.error(f"Erro ao ler: {e}")

    # 2. Seletor de Abas (Só aparece se já tiver carregado)
    if 'todas_abas_backup' in st.session_state:
        abas_disponiveis = list(st.session_state['todas_abas_backup'].keys())
        
        # Mostra qual está ativa
        aba_selecionada = st.selectbox(
            "Editando a aba:", 
            options=abas_disponiveis, 
            index=abas_disponiveis.index(st.session_state.get('aba_atual', abas_disponiveis[0]))
        )

        # 3. LÓGICA DE TROCA DE ABA (SALVAMENTO AUTOMÁTICO)
        # Se o usuário mudou a aba no menu...
        if aba_selecionada != st.session_state['aba_atual']:
            # A) Salva o trabalho da aba ANTERIOR no backup
            aba_velha = st.session_state['aba_atual']
            st.session_state['todas_abas_backup'][aba_velha] = st.session_state['df_ativo'].copy()
            
            # B) Carrega a NOVA aba para a tela
            novo_df = st.session_state['todas_abas_backup'][aba_selecionada]
            st.session_state['df_ativo'] = normalizar_df(novo_df)
            
            # C) Atualiza o nome da atual
            st.session_state['aba_atual'] = aba_selecionada
            st.rerun()

# --- 6. CONFIGURAÇÕES ---
with st.sidebar.expander("⚙️ Configurações"):
    api_key_ors = st.text_input("OpenRouteService Key:", type="password")
    raio_agrupamento = st.slider("Raio Agrupamento (m):", 100, 2000, 700, step=50)
    raio_agrupamento_real = raio_real_m(raio_agrupamento)

st.sidebar.markdown("---")

modo_visual = st.sidebar.radio("Modo de Trabalho:", ["Edição (Mapa/Tabela)", "Rotas (Simulação)", "Resumo Gerencial"], index=0)

# =========================================================
#               CORPO PRINCIPAL
# =========================================================

if modo_visual == "Edição (Mapa/Tabela)":
    
       
    # --- NOVIDADE: FILTRO DE ROTAS AGORA FICA AQUI EM CIMA ---
    # 1. Prepara a lista de rotas
    lista_rotas = st.session_state['df_ativo']['ROTA'].unique()
    rotas_db = sorted([str(r) for r in lista_rotas if str(r) not in ['nan', 'None', '', '0', '0.0']])
    opcoes_rotas = ["Novos (Sem Rota)"] + rotas_db 
    
    # 2. Cria as colunas do filtro (Estilo Dashboard)
    c_filt1, c_filt2 = st.columns([3, 1])
    with c_filt1:
        filtro_rotas_bar = st.multiselect("🎯 Filtrar Rotas no Mapa/Tabela:", options=opcoes_rotas, placeholder="Selecione para focar...")
        st.session_state["filtro_rotas_bar"] = filtro_rotas_bar

    with c_filt2:
        focar_sugestoes = st.checkbox("💡 Ver apenas Sugestões", help="Filtra o mapa com as rotas sugeridas.")

    # 3. Lógica do Checkbox de Sugestões (Filtra o mapa automaticamente)
    if focar_sugestoes:
        rotas_sugeridas = []
        # Pega passageiros sem rota e extrai o nome da sugestão "VAN 01 (..."
        df_n = st.session_state['df_ativo'][st.session_state['df_ativo']['ROTA'].astype(str).isin(['', 'nan', 'None'])]
        for s in df_n['SUGESTAO']:
            if s and '(' in str(s):
                r = str(s).split('(')[0].strip()
                rotas_sugeridas.append(r)
        
        if rotas_sugeridas:
            filtro_rotas_bar = list(set(rotas_sugeridas))
            st.toast(f"Focando em: {', '.join(filtro_rotas_bar)}", icon="💡")
        else:
            st.warning("Nenhuma sugestão encontrada para filtrar.")

    st.divider() 
    
    # AQUI CONTINUA O CÓDIGO NORMAL
    col_tab, col_map = st.columns([6, 4])
    ALTURA_FIXA = 500

    # =========================================================
    #      PREPARAÇÃO E CORREÇÕES (CRÍTICO)
    # =========================================================
    
    # 1. Status Seguro
    def get_status_icon(row):
        lat, lat_emb = row['LATITUDE CASA'], row['LATITUDE EMBARQUE']
        cidade = str(row.get('CIDADE', '')).upper()
        # --- NOVA REGRA: FRG ---
        if 'FAZENDA' in cidade or 'FRG' in cidade or 'RIO GRANDE' in cidade:
            return " 🚫  FORA DA ÁREA" # Marca específica para ignorar
    # -----------------------
        try:
            val = row.get('DIST_EMBARQUE_M', 0)
            dist = float(val) if pd.notna(val) and val != '' else 0.0
        except: dist = 0.0
        
        if lat_dest != 0 and lat != 0:
            try:
                if geodesic((lat, row['LONGITUDE CASA']), (lat_dest, lon_dest)).meters > 120000:
                    return "🏴‍☠️ ERRO >120KM"
            except: pass
        
        if lat == 0: return "🔴 SEM GPS CASA"
        if lat_emb == 0 and str(row.get('EMBARQUE','')).strip() != '': return "⚠️ SEM GPS PONTO"
        if dist > 2000: return f"⚠️ MUITO LONGE ({int(dist)}m)"
        if dist > 1001: return f"🔸 Longe ({int(dist)}m)"
        return "🟢 OK"

    # 2. Prepara Dados
    st.session_state['df_ativo'] = calcular_sugestoes_rota(st.session_state['df_ativo'])
    df_show = st.session_state['df_ativo'].copy()
    # ... (Logo após criar df_show e limpar as colunas) ...
    df_show = df_show.loc[:, ~df_show.columns.duplicated()]

    # ==============================================================================
    # 🔢 CÁLCULO INTELIGENTE DE PARADAS (RESET POR ROTA)
    # ==============================================================================
    # ==============================================================================
#  NUMERAÇÃO DE PARADAS (CORRIGIDO: usa ORDEM, não HORARIO)
# ==============================================================================

    mapa_paradas = {}

    # Agrupa os dados por ROTA
    grupos_rota = df_show.groupby('ROTA')

    for nome_rota, grupo in grupos_rota:
        if nome_rota in ['', 'nan', 'None', '0']:
            continue

        # ✅ Garante ORDEM numérica
        if 'ORDEM' in grupo.columns:
            grupo = grupo.copy()
            grupo['ORDEM'] = pd.to_numeric(grupo['ORDEM'], errors='coerce').fillna(10**9)

        # ✅ Ordena pela ORDEM (mantém a “ordem da planilha”)
        grupo = grupo.sort_values('ORDEM', kind='mergesort')

        # ✅ Agora pega os locais únicos na ordem em que aparecem (sem usar HORARIO)
        vistos = set()
        seq = 1
        for _, row in grupo.iterrows():
            lat = row.get('LATITUDE EMBARQUE', 0)
            lon = row.get('LONGITUDE EMBARQUE', 0)

            # proteção de NaN/None
            lat = float(lat) if pd.notna(lat) else 0.0
            lon = float(lon) if pd.notna(lon) else 0.0

            if lat != 0:
           # ✅ chave padronizada (tem que ser igual no get)
                chave = (str(nome_rota).strip().upper(), round(lat, 6), round(lon, 6))
                if chave not in vistos:
                    vistos.add(chave)
                    mapa_paradas[chave] = seq
                    seq += 1

    # # Função para aplicar na tabela com proteção contra valores vazios
    # def get_parada_rota(row):
    #     try:
    #         r = row['ROTA']
    #         val_lat = row.get('LATITUDE EMBARQUE', 0)
    #         val_lon = row.get('LONGITUDE EMBARQUE', 0)
    #         lat = float(val_lat) if pd.notna(val_lat) else 0.0
    #         lon = float(val_lon) if pd.notna(val_lon) else 0.0
    #         return mapa_paradas.get((r, lat, lon), 0)
    #     except Exception:
    #         return 0

    # df_show['PARADA'] = df_show.apply(get_parada_rota, axis=1)

    # ==============================================================================

    # 1. CRIA A COLUNA STATUS (Seu código continua aqui...)
    nome_coluna_status = "⚠️ STATUS"
    
    # >>> CORREÇÃO DEFINITIVA DE TIPOS (BLINDAGEM CONTRA FLOAT/INT) <<<
    # Garante que todas as colunas de texto sejam TEXTO, mesmo se vierem vazias ou numéricas do Excel
    cols_texto = ['MATRICULA', 'NOME', 'ENDERECO', 'BAIRRO', 'CIDADE', 'EMBARQUE', 'HORARIO', 'ROTA', 'TURNO', 'SUGESTAO']
    
    for col in cols_texto:
        # Se a coluna não existir, cria vazia
        if col not in df_show.columns:
            df_show[col] = ""
        # Se existir, força virar texto e remove 'nan'
        else:
            df_show[col] = df_show[col].astype(str).replace(['nan', 'None', 'NaN', '0.0'], '')

    # >>> CORREÇÃO DO CHECKBOX MARCADO SOZINHO <<<
    for col_sel in ['SEL_CASA', 'SEL_EMB', 'SEL_DES']:
        if col_sel in df_show.columns:
            df_show[col_sel] = df_show[col_sel].fillna(False).infer_objects(copy=False)
        else:
            df_show[col_sel] = False

    df_show = df_show.loc[:, ~df_show.columns.duplicated()]
    if 'DIST_EMBARQUE_M' not in df_show.columns: df_show['DIST_EMBARQUE_M'] = 0
# 1. CRIA A COLUNA (Padronizado: Sem espaço antes do ícone)
    # Copie e cole EXATAMENTE assim para garantir que o nome seja igual
    nome_coluna_status = "⚠️ STATUS" 
    
    df_show[nome_coluna_status] = [get_status_icon(r) for _, r in df_show.iterrows()]
    df_show[nome_coluna_status] = df_show[nome_coluna_status].astype(str)

    # =========================================================
    # 🛠️  MODO CORREÇÃO (FILTRO DE ERROS NA SIDEBAR)
    # =========================================================
    st.sidebar.markdown("---")
    modo_erro = st.sidebar.toggle("⚠️ Focar Apenas nos Erros", value=False)

    if modo_erro:
        # Filtra usando a MESMA variável 'nome_coluna_status' para não errar nunca mais
        mask_erro = (
            (df_show[nome_coluna_status].str.contains("ERRO")) | 
            (df_show[nome_coluna_status].str.contains("SEM GPS")) | 
            (df_show[nome_coluna_status].str.contains("LONGE")) |
            (df_show['LATITUDE CASA'] == 0)
        )
        df_show = df_show[mask_erro]
        
        if df_show.empty:
            st.toast("🎉 Tudo limpo! Nenhum erro encontrado.", icon="✅")
        else:
            st.toast(f"Focando em {len(df_show)} problemas.", icon="🚨")



    # =========================================================
    #           COLUNA DA ESQUERDA: TABELA
    # =========================================================
    with col_tab:
        c_sent1, c_sent2 = st.columns([2, 1])
        with c_sent1:
            sentido = st.radio("Sentido:", ["Ida (Entrada)", "Volta (Saída)"], horizontal=True)
            

        # --- 1. FILTROS PADRÃO (O que você já tinha) ---
        if sentido == "Ida (Entrada)":
            df_show = df_show[~df_show['HORARIO'].astype(str).str.lower().str.contains('x', na=False)]
        if 'ORDEM' not in df_show.columns:
            df_show['ORDEM'] = 10**9
        else:
            df_show['ORDEM'] = pd.to_numeric(df_show['ORDEM'], errors='coerce').fillna(10**9)

        df_show = df_show.sort_values(by=['ROTA', 'ORDEM'], kind='mergesort')

        # ✅ Recalcula PARADA depois do filtro do sentido (Ida já sem XXXXX)
        def get_parada_rota(row):
            try:
                r = str(row.get('ROTA', '')).strip().upper()
                val_lat = row.get('LATITUDE EMBARQUE', 0)
                val_lon = row.get('LONGITUDE EMBARQUE', 0)
                lat = float(val_lat) if pd.notna(val_lat) else 0.0
                lon = float(val_lon) if pd.notna(val_lon) else 0.0

                # ⚠️ opcional mas recomendado: arredondar p/ bater com o mapa
                lat = round(lat, 6)
                lon = round(lon, 6)

                return mapa_paradas.get((r, lat, lon), 0)
            except Exception:
                return 0

        # garante que existe
        if 'PARADA' not in df_show.columns:
            df_show['PARADA'] = 0

        df_show['PARADA'] = df_show.apply(get_parada_rota, axis=1)

        if filtro_rotas_bar:
            rotas_reais = [r for r in filtro_rotas_bar if r != "Novos (Sem Rota)"]
            mask = df_show['ROTA'].isin(rotas_reais) | df_show['ROTA'].isin(['', 'nan'])
            df_show = df_show[mask]

        if busca:
            mask = df_show['NOME'].str.lower().str.contains(busca.lower()) | \
                   df_show['ENDERECO'].str.lower().str.contains(busca.lower())
            df_show = df_show[mask]

        # =========================================================
        # 🎯 MODO FOCO (5KM) - A OTIMIZAÇÃO DE PERFORMANCE
        # =========================================================
        # Cria uma cópia focada. Se não tiver alvo, ela é igual ao df_show normal.
        target = st.session_state['edit_target']
        df_foco = df_show.copy() 
        msg_foco = ""

        if target:
            idx_alvo = target['index']
            tipo_alvo = target['tipo']
            
            # Pega GPS do alvo (Casa ou Embarque) para ser o centro do raio
            lat_t = st.session_state['df_ativo'].at[idx_alvo, 'LATITUDE CASA']
            lon_t = st.session_state['df_ativo'].at[idx_alvo, 'LONGITUDE CASA']
            
            if tipo_alvo == 'SEL_EMB':
                 lat_e = st.session_state['df_ativo'].at[idx_alvo, 'LATITUDE EMBARQUE']
                 if lat_e != 0: 
                     lat_t, lon_t = lat_e, st.session_state['df_ativo'].at[idx_alvo, 'LONGITUDE EMBARQUE']
            
            # Se o alvo tem GPS, filtra os vizinhos num raio de 5km
            if lat_t != 0:
                indices_no_raio = []
                indices_no_raio.append(idx_alvo) # Sempre inclui o próprio alvo
                
                for i, r in df_show.iterrows():
                    if i == idx_alvo: continue
                    
                    # Compara com o GPS do vizinho
                    l_viz = r['LATITUDE EMBARQUE'] if r['LATITUDE EMBARQUE'] != 0 else r['LATITUDE CASA']
                    ln_viz = r['LONGITUDE EMBARQUE'] if r['LONGITUDE EMBARQUE'] != 0 else r['LONGITUDE CASA']
                    
                    if l_viz != 0:
                        # Distância rápida (Geodesic)
                        d = geodesic((lat_t, lon_t), (l_viz, ln_viz)).meters
                        if d <= 5000: # 5 KM
                            indices_no_raio.append(i)
                
                # APLICA O FILTRO!
                df_foco = df_show.loc[indices_no_raio]
                msg_foco = f"🔎 Modo Foco: Exibindo {len(df_foco)} vizinhos em 5km"

        # ... (O código continua abaixo com tab_tabela, etc.) ...

        # Configuração Colunas
        cnf_padrao = {
            "ORDEM": st.column_config.NumberColumn("ORDEM", format="%d", width="small"),
            "⚠️ STATUS": st.column_config.TextColumn("Status", width="small", disabled=True),
            "PARADA": st.column_config.NumberColumn("Parada", format="%d", width="small", disabled=True),
            "SUGESTAO": st.column_config.TextColumn("💡 Sugestão", disabled=True),
            "SEL_CASA": st.column_config.CheckboxColumn("🏠", width="small"),
            "SEL_EMB": st.column_config.CheckboxColumn("🚏", width="small"),
            "SEL_DES": st.column_config.CheckboxColumn("🏁", width="small"),
            "MATRICULA": st.column_config.TextColumn("Matrícula"),
            "NOME": st.column_config.TextColumn("Nome", width="small"),
            "ENDERECO": st.column_config.TextColumn("Endereço", width="medium"),
            "EMBARQUE": st.column_config.TextColumn("Ponto de Embarque", width="medium"),
            "BAIRRO": st.column_config.TextColumn("Bairro"),
            "CIDADE": st.column_config.TextColumn("Cidade"),
            "HORARIO": st.column_config.TextColumn("Horário"),
            "ROTA": st.column_config.TextColumn("Rota"),
            "TURNO": st.column_config.TextColumn("Turno"),
            "SEL_APAGAR": st.column_config.CheckboxColumn("🗑️ Apagar", width="small"),
            "DIST_EMBARQUE_M": st.column_config.NumberColumn("Caminhada (m)", format="%d m", disabled=True),
            
        }
        
        cols_visiveis = [
            'ORDEM','PARADA','⚠️ STATUS', 'SEL_CASA', 'SEL_EMB', 'NOME', 'ENDERECO',  
            'EMBARQUE','HORARIO','ROTA','CIDADE','VEICULO','TURNO', 'SEL_APAGAR' ,'DIST_EMBARQUE_M', 'SEL_DES', 'MATRICULA','BAIRRO'
        ]
        
        cols_ex = [c for c in cols_visiveis if c in df_show.columns]
        

        tab_tabela, tab_novos, tab_erros, tab_export, tab_logs, tab_Otimi, tab_rotas = st.tabs(["📊 Geral", "🆕 Novos", "🚨 Erros", "📲 Export", "📜 Histórico","⚡ Otimização", "🗺️ Rotas"])
        with tab_tabela:
            target = st.session_state['edit_target']
            
            # Prepara a visualização
            df_tabela_view = df_foco.copy()
            if msg_foco: st.caption(msg_foco)

            # =========================================================
            # 🔒 1. TRAVA DE SEGURANÇA (O "LOCK")
            # =========================================================
            # Garante que o alvo atual esteja marcado visualmente antes de filtrar
            if target:
                idx_alvo = target['index']
                col_alvo = target['tipo']
                if idx_alvo in df_tabela_view.index:
                    df_tabela_view.at[idx_alvo, col_alvo] = True

            # =========================================================
            # 2. FILTRO DE FOCO (MANTENDO O QUE VOCÊ TINHA)
            # =========================================================
            if target and target['index'] in df_tabela_view.index:
                # Filtra para mostrar SÓ a linha sendo editada
                df_tabela_view = df_tabela_view.loc[[target['index']]]
                # Mostra o aviso azul com o nome
                st.info(f"✏️ Editando: **{df_tabela_view.iloc[0]['NOME']}**")

            if "SEL_APAGAR" not in df_show.columns:
                df_show["SEL_APAGAR"] = False
            else:
                df_show["SEL_APAGAR"] = df_show["SEL_APAGAR"].fillna(False).astype(bool)


            # =========================================================
            # 3. EXIBE A TABELA
            # =========================================================
            df_editado = st.data_editor(
                df_tabela_view[cols_ex], 
                height=ALTURA_FIXA if not target else 150, 
                #use_container_width=True,
                width="stretch", 
                hide_index=True, 
                column_config=cnf_padrao, 
                key="editor_main"
            )
            
            # --- 4. SALVAR EDIÇÕES DE TEXTO/NUMERO ---
            mudou = False

            for idx, row in df_editado.iterrows():
                if idx not in st.session_state['df_ativo'].index:
                    continue

                for col in df_editado.columns:
                    if col in ['⚠️ STATUS', 'DIST_EMBARQUE_M', 'SUGESTAO', 'SEL_CASA', 'SEL_EMB', 'SEL_DES', 'SEL_APAGAR']:
                        continue

                    vn = row[col]
                    if col not in st.session_state['df_ativo'].columns:
                        continue

                    va = st.session_state['df_ativo'].at[idx, col]
                    if str(vn) != str(va):

                        col_upper = col.upper()

                        # =========================================================
                        # 🔴 CASO ORDEM (só permitido com 1 rota filtrada)
                        # =========================================================
                        if col_upper == "ORDEM":
                            df = st.session_state["df_ativo"]

                            rota_linha = str(df.at[idx, "ROTA"]).strip().upper()

                            filtro = st.session_state.get("filtro_rotas_bar", [])
                            filtro_ok = isinstance(filtro, list) and len(filtro) == 1
                            rota_foco = str(filtro[0]).strip().upper() if filtro_ok else ""

                            if (not filtro_ok) or (rota_foco in ["NOVOS (SEM ROTA)", ""]) or (rota_linha != rota_foco):
                                st.toast("⚠️ Para mudar ORDEM, filtre UMA rota (a mesma da linha) no topo.", icon="🔒")
                                continue

                            try:
                                old_ord = int(pd.to_numeric(va, errors="coerce"))
                            except:
                                old_ord = 0

                            try:
                                new_ord = int(pd.to_numeric(vn, errors="coerce"))
                            except:
                                new_ord = old_ord

                            if new_ord <= 0:
                                new_ord = 1

                            m_rota = df["ROTA"].astype(str).str.strip().str.upper() == rota_linha
                            df.loc[m_rota, "ORDEM"] = pd.to_numeric(df.loc[m_rota, "ORDEM"], errors="coerce").fillna(0).astype(int)

                            n = int(df.loc[m_rota].shape[0])
                            if new_ord > n:
                                new_ord = n

                            if old_ord <= 0:
                                old_ord = n

                            if new_ord != old_ord:
                                if new_ord < old_ord:
                                    mask = m_rota & (df["ORDEM"] >= new_ord) & (df["ORDEM"] < old_ord) & (df.index != idx)
                                    df.loc[mask, "ORDEM"] = df.loc[mask, "ORDEM"] + 1
                                else:
                                    mask = m_rota & (df["ORDEM"] <= new_ord) & (df["ORDEM"] > old_ord) & (df.index != idx)
                                    df.loc[mask, "ORDEM"] = df.loc[mask, "ORDEM"] - 1

                            df.at[idx, "ORDEM"] = new_ord

                            df_rota = df.loc[m_rota].sort_values(["ORDEM"], kind="mergesort")
                            df.loc[df_rota.index, "ORDEM"] = range(1, len(df_rota) + 1)

                            st.session_state["df_ativo"] = df
                            mudou = True
                            continue

                        # =========================================================
                        # ✅ PARA OUTRAS COLUNAS: salva normal
                        # =========================================================
                        st.session_state['df_ativo'].at[idx, col] = vn
                        mudou = True

                        # CASO ENDERECO / etc...
                        if col_upper == 'ENDERECO':
                            
                            
                            try:
                                cidade = st.session_state['df_ativo'].at[idx, 'CIDADE']
                            except:
                                cidade = ""

                            try:
                                lat, lon, tipo = buscar_gps_unico(vn, "", cidade)
                                st.session_state['df_ativo'].at[idx, 'LATITUDE CASA'] = lat
                                st.session_state['df_ativo'].at[idx, 'LONGITUDE CASA'] = lon
                                st.session_state['df_ativo'].at[idx, 'TIPO_GEO'] = tipo
                            except Exception as e:
                                st.warning(f"Falha ao buscar GPS da casa: {e}")

                            # 🔴 PASSO 4: recalcula distância automaticamente
                            try:
                                st.session_state['df_ativo'] = recalcular_distancia_1_linha(
                                    st.session_state['df_ativo'],
                                    idx
                                )
                            except Exception:
                                pass
                                    
                        # =========================================================
                        # 🔴 CASO 2: MUDOU EMBARQUE
                        # =========================================================
                        elif col_upper == 'EMBARQUE':
                            try:
                                lat, lon, tipo = buscar_gps_unico(vn, is_pickup=True)
                                st.session_state['df_ativo'].at[idx, 'LATITUDE EMBARQUE'] = lat
                                st.session_state['df_ativo'].at[idx, 'LONGITUDE EMBARQUE'] = lon
                                st.session_state['df_ativo'].at[idx, 'TIPO_GEO'] = tipo
                            except Exception as e:
                                st.warning(f"Falha ao buscar GPS do embarque: {e}")

                            # 🔴 PASSO 4: recalcula distância automaticamente
                            try:
                                st.session_state['df_ativo'] = recalcular_distancia_1_linha(
                                    st.session_state['df_ativo'],
                                    idx
                                )
                            except Exception:
                                pass

            
            # =========================================================
            # 5. LÓGICA DE SELEÇÃO INTELIGENTE
            # =========================================================
            cols_sel = ['SEL_CASA', 'SEL_EMB', 'SEL_DES']
            novo_alvo = None
            
            for idx, row in df_editado.iterrows():
                for col in cols_sel:
                    # Se está marcado
                    if row[col] == True:
                        # Verifica se é uma NOVA seleção (clicou em outro ou re-clicou)
                        if not target or target['index'] != idx or target['tipo'] != col:
                            novo_alvo = {'index': idx, 'tipo': col}
            
            # Se clicou em alguém novo, muda o alvo
            if novo_alvo:
                st.session_state['df_ativo'].at[novo_alvo['index'], novo_alvo['tipo']] = True
                st.session_state['edit_target'] = novo_alvo
                st.session_state['ponto_provisorio'] = None # Reseta o pino vermelho
                st.rerun()

            # Se tentou desmarcar o atual na tabela, o código ignora 
            # e a Trava (Passo 1) remarca ele no próximo refresh.
            # =========================================================
            # 🗑️ EXCLUIR PASSAGEIRO (CONFIRMAÇÃO)
            # =========================================================
            marcados = []
            if "SEL_APAGAR" in df_editado.columns:
                marcados = df_editado.index[df_editado["SEL_APAGAR"] == True].tolist()

            if len(marcados) > 0:
                st.markdown("---")
                st.warning(f"🗑️ Você marcou {len(marcados)} passageiro(s) para apagar. Confirma?")

                cols_show = [c for c in ["NOME", "ROTA", "EMBARQUE", "HORARIO", "ORDEM"] if c in df_editado.columns]
                if cols_show:
                    st.dataframe(df_editado.loc[marcados, cols_show], width="stretch")

                c1, c2 = st.columns([1, 1])
                with c1:
                    confirmar = st.button("✅ Confirmar exclusão", type="primary")
                with c2:
                    cancelar = st.button("↩️ Cancelar", type="secondary")

                if cancelar:
                    # só limpa a marcação visual (vai sumir no rerun)
                    df_editado.loc[marcados, "SEL_APAGAR"] = False
                    st.toast("Cancelado.", icon="↩️")
                    st.rerun()

                if confirmar:
                    df_all = st.session_state["df_ativo"].copy()

                    # (opcional) lixeira
                    st.session_state.setdefault("df_lixeira", pd.DataFrame())
                    st.session_state["df_lixeira"] = pd.concat(
                        [st.session_state["df_lixeira"], df_all.loc[marcados].copy()],
                        ignore_index=True
                    )

                    df_all = df_all.drop(index=marcados, errors="ignore").copy()
                    st.session_state["df_ativo"] = df_all

                    st.toast(f"🗑️ Apagados {len(marcados)} passageiro(s).", icon="🗑️")
                    st.rerun()
            if mudou: 
                st.session_state['df_ativo'] = normalizar_df(st.session_state['df_ativo'])
                st.rerun()

        with tab_Otimi:
            st.markdown("### 🛠️ Ferramentas de Roteirização (OSRM)")
            
            # Criar colunas para os botões ficarem alinhados
            col_btn1, col_btn2, col_btn3 = st.columns(3)
            
            with col_btn1:
                if st.button("🚀 Otimizar Todas as Rotas", use_container_width=True):
                    # Aqui vai sua chamada para aplicar_todos ou similar
                    with st.spinner("Otimizando..."):
                        st.session_state['df_ativo'] = aplicar_todos(st.session_state['df_ativo'], lat_dest, lon_dest)
                        st.success("Todas as rotas foram otimizadas via OSRM!")
                        st.rerun()

            with col_btn2:
                if st.button("🧹 Corrigir Sequências", use_container_width=True):
                    # Chame sua função de renumerar_ordem_rota ou corrigir_duplicados
                    st.session_state['df_ativo'] = renumerar_ordem_rota(st.session_state['df_ativo'])
                    st.success("Ordens renumeradas!")
                    st.rerun()

            with col_btn3:
                # Se você tiver um seletor de rota específica para OSRM:
                rota_para_osrm = st.selectbox("Selecionar Rota p/ OSRM:", options=rotas_db, key="sel_osrm_especifica")
                if st.button(f"Otimizar {rota_para_osrm}", use_container_width=True):
                    # Lógica para uma rota só
                    pass

        with tab_rotas:
            rota_alvo = st.selectbox("Rota", sorted(st.session_state["df_ativo"]["ROTA"].dropna().unique()))
            hora_alvo = st.text_input("Chegar no cliente às", value="06:37")
            parada_min = st.number_input("Parada por ponto (min)", min_value=0, max_value=10, value=0)

            # ✅ NÃO sobrescrever lat_dest/lon_dest do resto do app
            lat_dest_osrm = st.session_state.get("lat_dest", 0)
            lon_dest_osrm = st.session_state.get("lon_dest", 0)

            # garante número
            try:
                lat_dest_osrm = float(lat_dest_osrm)
            except:
                lat_dest_osrm = 0.0
            try:
                lon_dest_osrm = float(lon_dest_osrm)
            except:
                lon_dest_osrm = 0.0

            destino_osrm = None
            if lat_dest_osrm == 0.0 or lon_dest_osrm == 0.0:
                st.error("Destino não definido (lat_dest/lon_dest).")
            else:
                destino_osrm = (lat_dest_osrm, lon_dest_osrm)

            if destino_osrm is not None:
                if st.button("Simular Hr Rota", key="btn_sim_osrm"):
                    df_prev, msg = simular_rota(st.session_state["df_ativo"], rota_alvo, destino_osrm, hora_alvo, parada_min)
                    st.session_state["df_prev"] = df_prev
                    st.success(msg)

                    view = df_prev[df_prev["ROTA"].astype(str).str.upper() == str(rota_alvo).upper()][
                        ["ORDEM", "NOME", "HORARIO", "HORARIO_PREV"]
                    ]
                    st.dataframe(view, use_container_width=True)

                if st.button("Aplicar (somente quem está sem HORARIO)", key="btn_aplicar_vazios"):
                    if "df_prev" not in st.session_state:
                        st.error("Simule primeiro.")
                    else:
                        df_ap, qtd = aplicar_so_vazios(st.session_state["df_prev"], rota_alvo)
                        st.session_state["df_ativo"] = df_ap
                        st.success(f"Preenchi {qtd} horários (somente vazios).")

                if st.button("Aplicar (TODOS da rota)", key="btn_aplicar_todos"):
                    if "df_prev" not in st.session_state:
                        st.error("Simule primeiro.")
                    else:
                        df_ap, qtd = aplicar_todos(st.session_state["df_prev"], rota_alvo)
                        st.session_state["df_ativo"] = df_ap
                        st.success(f"Atualizei {qtd} horários (todos da rota).")

            filtro = st.session_state.get("filtro_rotas_bar", [])
            rota_ok = (filtro and len(filtro) == 1 and filtro[0] != "Novos (Sem Rota)")

            if not rota_ok:
                st.info("Selecione apenas 1 rota no filtro (lá em cima) para recalcular o traçado.")
            else:
                rota_focada = str(filtro[0]).strip().upper()

                if st.button("🔄 Recalcular Traçado desta rota", width="stretch"):
                    st.success(f"Solicitado recálculo OSRM da rota {rota_focada}.")
                    st.rerun()

# --- ABA 2: NOVOS (TRIAGEM) - AQUI ESTÁ A NOVIDADE ---
       # =========================================================
        # ABA 2: NOVOS (COM PERMUTA, LOTAÇÃO E CIDADE) - CÓDIGO COMPLETO
        # =========================================================
        with tab_novos:
            st.markdown("### 🆕 Gestão de Novos Passageiros (Inteligente)")
            st.caption("Ao definir a rota: 1. Tenta agrupar com vizinho. 2. Se não der, cria ponto na casa e recalcula trajeto real (OSRM).")
            
            # 1. Configurações
            c_h1, c_h2 = st.columns([1, 4])
            with c_h1:
                hora_limite = st.text_input("🏁 Chegada:", value="06:37")
            
            # 2. Filtra quem está sem rota
            mask_novos = df_show['ROTA'].astype(str).isin(['', 'nan', 'None', '0', '0.0'])
            df_novos = df_show[mask_novos].copy()

            
            if df_novos.empty:
                st.success("🎉 Todos os novos passageiros já foram roteirizados!")
            else:
                st.info(f"Há {len(df_novos)} passageiro(s) aguardando rota.")
                cols_triagem = ['SEL_CASA','SEL_EMB','SEL_DES','NOME','BAIRRO','ENDERECO','SUGESTAO','ROTA','EMBARQUE','HORARIO']

                df_edit_novos = st.data_editor(
                    df_novos[cols_triagem],
                    height=400,
                    use_container_width=True,
                    hide_index=True,
                    key="editor_novos",


                    column_config={
                        "NOME": st.column_config.TextColumn("Funcionário", disabled=True),
                        "BAIRRO": st.column_config.TextColumn("Bairro", disabled=True, width="small"),
                        "ENDERECO": st.column_config.TextColumn("Endereço", disabled=True, width="medium"),
                        "SUGESTAO": st.column_config.TextColumn("⭐ Sugestão", disabled=True, width="medium"),
                        "ROTA": st.column_config.TextColumn("Definir Rota ✏️", width="small", required=True),
                        "EMBARQUE": st.column_config.TextColumn("Ponto", disabled=True),
                        "HORARIO": st.column_config.TextColumn("Horário", disabled=True, width="small"),
                        "SEL_CASA": st.column_config.CheckboxColumn("🏠", width="small"),
                        "SEL_EMB":  st.column_config.CheckboxColumn("🚏", width="small"),
                        "SEL_DES":  st.column_config.CheckboxColumn("🏁", width="small"),
                    }
                )
                                # =========================================================
                # 🎯 Se marcou 🏠/🚏/🏁 em NOVOS -> vira alvo (edit_target)
                # =========================================================
                cols_sel = [c for c in ['SEL_CASA','SEL_EMB','SEL_DES'] if c in st.session_state['df_ativo'].columns]
                target = st.session_state.get('edit_target')

                novo_alvo_novos = None
                for idx, row in df_edit_novos.iterrows():
                    for col in cols_sel:
                        try:
                            if row.get(col) == True:
                                if (not target) or target.get('index') != idx or target.get('tipo') != col:
                                    novo_alvo_novos = {'index': idx, 'tipo': col}
                        except:
                            pass

                if novo_alvo_novos:
                    st.session_state['df_ativo'].at[novo_alvo_novos['index'], novo_alvo_novos['tipo']] = True
                    st.session_state['edit_target'] = novo_alvo_novos
                    st.session_state['ponto_provisorio'] = None
                    st.rerun()
                
                mudou_novos = False
                
                for idx, row in df_edit_novos.iterrows():
                    if mudou_novos:
                        st.rerun()

                    if idx in st.session_state['df_ativo'].index:
                        import re
                        raw_rota = str(row['ROTA']).upper()
                        nova_rota = re.sub(r'\s+', ' ', raw_rota).strip()
                        # ✅ GATE (OPÇÃO 3): só continua quando a rota estiver "completa"
                        # Ex.: "VAN 01", "MICRO 3", "ONIBUS 12"
                        # Enquanto estiver "V", "VA", "VAN" etc. -> não faz nada
                        if not re.match(r'^(VAN|MICRO|ONIBUS|ÔNIBUS)\s+\d{1,3}$', nova_rota):
                            continue
                        rota_antiga = str(st.session_state['df_ativo'].at[idx, 'ROTA']).strip().upper()
                       # --- GATILHO: MUDOU A ROTA ---

                        if nova_rota != rota_antiga and nova_rota not in ['NAN', 'NONE', '', '0', '0.0']:

                            df = st.session_state['df_ativo']

                            # 1) Salva a nova rota
                            df.at[idx, 'ROTA'] = nova_rota

                            # Turno padrão se vazio
                            if 'TURNO' in df.columns:
                                if not str(df.at[idx, 'TURNO']).strip() or str(df.at[idx, 'TURNO']).strip().lower() in ['nan', 'none']:
                                    df.at[idx, 'TURNO'] = '1°T'

                            # 2) Tenta ÍMÃ UMA ÚNICA VEZ (se tiver GPS casa)
                            usou_ima = False
                            try:
                                from otimizador import usar_ponto_existente_proximo
                                lat_casa = float(df.at[idx, 'LATITUDE CASA'] or 0)
                                lon_casa = float(df.at[idx, 'LONGITUDE CASA'] or 0)

                                if lat_casa != 0 and lon_casa != 0:
                                    usou_ima, msg_ima = usar_ponto_existente_proximo(
                                        df, idx, nova_rota, raio_max_caminhada=raio_real_m(raio_agrupamento)
                                    )
                                    if usou_ima:
                                        st.toast(f"✅ {msg_ima}", icon="🧲")

                                        # garante ORDEM limpinha 1..N na rota (sem duplicar)
                                        df = st.session_state['df_ativo']  # garante que df existe aqui
                                        m_rota = df['ROTA'].astype(str).str.strip().str.upper() == nova_rota
                                        df.loc[m_rota, 'ORDEM'] = pd.to_numeric(df.loc[m_rota, 'ORDEM'], errors='coerce').fillna(0).astype(int)

                                        df_rota = df[m_rota].sort_values('ORDEM', kind='mergesort')
                                        df.loc[df_rota.index, 'ORDEM'] = range(1, len(df_rota) + 1)

                                        st.session_state['df_ativo'] = df  # salva de volta
                                    else:
                                        st.toast(f"📍 {msg_ima}. Vou usar a CASA como embarque.", icon="⚠️")
                            except Exception:
                                usou_ima = False

                            # 3) Se não usou ímã, usa CASA como EMBARQUE (NÃO geocodifica)
                            try:
                                lat_e = float(df.at[idx, 'LATITUDE EMBARQUE'] or 0)
                                lon_e = float(df.at[idx, 'LONGITUDE EMBARQUE'] or 0)
                            except:
                                lat_e, lon_e = 0.0, 0.0

                            if (not usou_ima) and (lat_e == 0.0 or lon_e == 0.0):
                                try:
                                    lat_c = float(df.at[idx, 'LATITUDE CASA'] or 0)
                                    lon_c = float(df.at[idx, 'LONGITUDE CASA'] or 0)
                                except:
                                    lat_c, lon_c = 0.0, 0.0

                                if lat_c != 0.0 and lon_c != 0.0:
                                    df.at[idx, 'LATITUDE EMBARQUE'] = lat_c
                                    df.at[idx, 'LONGITUDE EMBARQUE'] = lon_c
                                    df.at[idx, 'EMBARQUE'] = df.at[idx, 'ENDERECO']
                                    df.at[idx, 'DIST_EMBARQUE_M'] = 0

                                # ✅ recalcula distância do passageiro que acabou de mudar GPS
                            st.session_state['df_ativo'] = recalcular_distancia_1_linha(df, idx)
                            # ✅ CORREÇÃO 1: garantir ORDEM e calcular HORARIO mesmo quando não achou ponto próximo

                            # atualiza df com o retorno do recalcular_distancia_1_linha
                            df = st.session_state['df_ativo']

                            if not usou_ima:

                                # 3.1) Garantir que ORDEM exista e tenha valor (se não tiver, coloca no final da rota)
                                try:
                                    if 'ORDEM' not in df.columns:
                                        df['ORDEM'] = 0

                                    ordem_atual = pd.to_numeric(df.at[idx, 'ORDEM'], errors='coerce')
                                    if pd.isna(ordem_atual) or int(ordem_atual) <= 0:
                                        ord_max_rota = pd.to_numeric(
                                            df.loc[df['ROTA'].astype(str).str.upper().str.strip() == nova_rota, 'ORDEM'],
                                            errors='coerce'
                                        ).fillna(0).max()
                                        df.at[idx, 'ORDEM'] = int(ord_max_rota) + 1
                                except:
                                    pass

                                # 3.2) Se não usou ímã, calcular horário do novo (encaixe)
                                try:
                                    from otimizador import inserir_e_otimizar_osrm

                                    lat_d = st.session_state.get("lat_dest", 0)
                                    lon_d = st.session_state.get("lon_dest", 0)

                                    if not lat_d and "lat_dest" in locals():
                                        lat_d = lat_dest
                                    if not lon_d and "lon_dest" in locals():
                                        lon_d = lon_dest

                                    hora_alvo = hora_limite

                                    lat_d = st.session_state.get("lat_dest", 0)
                                    lon_d = st.session_state.get("lon_dest", 0)
                                    hora_alvo = hora_limite

                                    if lat_d and lon_d:
                                        df = inserir_ponto_cirurgico_por_ordem(
                                            df,
                                            idx,
                                            nova_rota,
                                            lat_d,
                                            lon_d,
                                            hora_chegada_target=hora_alvo,
                                            modo_saida=False
                                            
                                        )
                                except Exception as e:
                                    st.warning(f"Não consegui calcular horário do novo: {e}")

                                st.session_state['df_ativo'] = df


                            # 4) RECALCULO CIRÚRGICO (somente para trás)
                            #    entrou na ORDEM N -> recalcula N-1, N-2... 1
                            # if not usou_ima:

                            #     try:
                            #         from otimizador import recalcular_horarios_cirurgico
                            #         ordem_novo = int(df.at[idx, 'ORDEM']) if pd.notna(df.at[idx, 'ORDEM']) else None

                            #         if ordem_novo and ordem_novo > 1:
                            #             df = recalcular_horarios_cirurgico(
                            #                 df,
                            #                 nova_rota,
                            #                 ordem_novo,
                            #                 buffer_min=2,
                            #                 modo_saida=False  # ENTRADA (XXXXX fora)
                            #             )

                            #     except Exception as e:
                            #         st.warning(f"Não consegui recalcular horário cirúrgico: {e}")

                            # 5) Salva DF e registra logs 1 vez
                            st.session_state['df_ativo'] = df

                            try:
                                st.session_state['df_ativo'] = registrar_alteracao_rota(st.session_state['df_ativo'], nova_rota)

                                p_nome = st.session_state['df_ativo'].at[idx, 'NOME']
                                registrar_log("INSERCAO", f"{p_nome} -> {nova_rota} (OK)", nova_rota)
                            except:
                                pass

                            mudou_novos = True
                            st.session_state['df_ativo'] = df
                            st.rerun()
                            break

                                            
        with tab_erros:
            st.markdown("### 🛠️ Correção Rápida")
            mask_erro = (df_show['⚠️ STATUS'].str.contains("ERRO")) | \
                        (df_show['⚠️ STATUS'].str.contains("SEM GPS")) | \
                        (df_show['DIST_EMBARQUE_M'] > 2000)
            df_erros = df_show[mask_erro].copy()
            
            if df_erros.empty: st.success("🎉 Nenhum erro crítico!")
            else:
                st.warning(f"⚠️ {len(df_erros)} passageiros com problemas.")
                df_erro_edit = st.data_editor(
                    df_erros[cols_ex], height=500, use_container_width=True, 
                    hide_index=True, column_config=cnf_padrao, key="editor_erros"
                )
                
                mudou_erro = False; novo_alvo_erro = None
                for idx, row in df_erro_edit.iterrows():
                    if idx in st.session_state['df_ativo'].index:
                         for col in df_editado.columns:
                             if col in row and col not in ['⚠️ STATUS', 'DIST_EMBARQUE_M']:
                                # ✅ Colunas "visuais" / calculadas não existem no df_ativo (ex: PARADA)
                                if col == 'PARADA':
                                    continue
                                if col not in st.session_state['df_ativo'].columns:
                                    continue 
                                vn, va = row[col], st.session_state['df_ativo'].at[idx, col]
                                if str(vn) != str(va):
                                    st.session_state['df_ativo'].at[idx, col] = vn; mudou_erro = True
                    for col in cols_sel:
                        if row[col] == True:
                             if not target or target['index'] != idx or target['tipo'] != col:
                                 novo_alvo_erro = {'index': idx, 'tipo': col}

                if novo_alvo_erro:
                    st.session_state['df_ativo'][cols_sel] = False
                    st.session_state['df_ativo'].at[novo_alvo_erro['index'], novo_alvo_erro['tipo']] = True
                    st.session_state['edit_target'] = novo_alvo_erro
                    st.session_state['ponto_provisorio'] = None; st.rerun()

                if mudou_erro: st.session_state['df_ativo'] = normalizar_df(st.session_state['df_ativo']); st.rerun()

        with tab_logs:
            st.markdown("### 📜 Histórico de Alterações")
            if os.path.exists(FILE_LOGS):
                logs = load_json(FILE_LOGS, [])
                if logs:
                    df_logs = pd.DataFrame(logs)
                    st.dataframe(df_logs, use_container_width=True, height=400)
                else:
                    st.info("Nenhuma alteração registrada ainda.")
            else:
                st.info("Nenhuma alteração registrada ainda.")

    with tab_export:
        st.divider()
        st.markdown("### 📲 Exportar Dados (Correção de Abas)")

        try:
            import io
            from datetime import datetime,timedelta

            # --- 1. NORMALIZAÇÃO AUTOMÁTICA DE TODAS AS ABAS ---
            dicionario_final = {}
            
            # Verifica se existem abas carregadas no backup
            if 'todas_abas_backup' in st.session_state and st.session_state['todas_abas_backup']:
                # Primeiro, garante que a aba que você está vendo agora está atualizada no backup
                aba_atual_nome = st.session_state.get('aba_atual', 'Planilha1')
                st.session_state['todas_abas_backup'][aba_atual_nome] = st.session_state['df_ativo'].copy()
                
                # Agora, percorre cada aba e força a normalização (ajusta ROTA, NOME, etc.)
                with st.spinner("Lendo e corrigindo todas as abas..."):
                    for nome_aba, df_raw in st.session_state['todas_abas_backup'].items():
                        # A mágica acontece aqui: normaliza cada aba antes de exportar
                        dicionario_final[nome_aba] = normalizar_df(df_raw.copy())
            else:
                dicionario_final = {"Rotas": st.session_state['df_ativo'].copy()}

            # --- 1. FUNÇÕES AUXILIARES ---
            def safe_get_km(valor_bruto):
                try:
                    if isinstance(valor_bruto, dict):
                        valor_bruto = valor_bruto.get('km', 0)
                    val_str = str(valor_bruto).upper().replace(',', '.').replace('KM', '').strip()
                    return float(val_str)
                except: return 0.0

            def somar_minutos(horario_str, minutos):
                try:
                    h_str = str(horario_str).strip().upper().replace('H', ':').replace('.', ':')
                    if ':' in h_str:
                        parts = h_str.split(':')
                        h_str = f"{int(parts[0]):02d}:{int(parts[1]):02d}"
                    ref = datetime.strptime(h_str, "%H:%M")
                    nova_hora = ref + timedelta(minutes=int(minutos))
                    return nova_hora.strftime("%Hh%M")
                except: return "??:??"

            # Prepara variáveis
            cliente_selecionado = 'cli_sel' in locals() and cli_sel != "-- Selecione --"
            nome_destino = str(cli_sel).upper().strip() if cliente_selecionado else "FÁBRICA"
            
            cache_meta = st.session_state.get('mapa_km_cache', {}) 
            try: rotas_mexidas_json = get_rotas_alteradas_historico()
            except: rotas_mexidas_json = []

            # --- 2. PREPARAÇÃO DOS DADOS (usa dicionario_final já normalizado) ---
            if dicionario_final:
                dicionario_abas_para_exportar = dicionario_final
            else:
                dicionario_abas_para_exportar = {"Rotas": normalizar_df(st.session_state['df_ativo'].copy())}

            # --- 3. GERAÇÃO DO ARQUIVO ---
            if dicionario_abas_para_exportar:
                buffer_fmt = io.BytesIO()
                with pd.ExcelWriter(buffer_fmt, engine='xlsxwriter') as writer:
                    workbook = writer.book
                    
                    # Estilos (Mantidos)
                    fmt_head = workbook.add_format({'bold': True, 'bg_color': '#FFC000', 'border': 1, 'align': 'center', 'valign': 'vcenter', 'font_size': 10})
                    fmt_subhead = workbook.add_format({'bold': True, 'bg_color': '#DDD9C4', 'border': 1, 'align': 'center', 'font_size': 9})
                    fmt_foot = workbook.add_format({'bold': True, 'bg_color': '#D9D9D9', 'border': 1, 'align': 'center', 'font_size': 10})
                    fmt_cell = workbook.add_format({'border': 1, 'align': 'left', 'font_size': 9})
                    fmt_center = workbook.add_format({'border': 1, 'align': 'center', 'font_size': 9})

                    for nome_aba, df_bruto in dicionario_abas_para_exportar.items():
                        nome_safe = str(nome_aba)[:31]
                        worksheet = workbook.add_worksheet(nome_safe)
                        
                        df_print = df_bruto.copy()

                        # ==========================================================
                        # ✅ CORREÇÃO: cache por ABA (compatível com cache antigo)
                        # Se mapa_km_cache estiver no formato novo:
                        #   cache_meta = { "Aba1": {"VAN 01": {...}}, "Aba2": {"VAN 01": {...}} }
                        # então usamos cache_aba = cache_meta[nome_aba]
                        # Se estiver no formato antigo (por rota direto), cai no fallback e usa cache_meta
                        # ==========================================================
                        cache_aba = {}
                        try:
                            if isinstance(cache_meta, dict) and (nome_aba in cache_meta) and isinstance(cache_meta.get(nome_aba), dict):
                                cache_aba = cache_meta.get(nome_aba, {})
                            else:
                                cache_aba = cache_meta if isinstance(cache_meta, dict) else {}
                        except:
                            cache_aba = cache_meta if isinstance(cache_meta, dict) else {}
                        # ==========================================================
                        
                        # Tratamento de Rotas (Ordenação Numérica)
                        import re
                        if 'ROTA' in df_print.columns:
                            df_print['ROTA'] = df_print['ROTA'].apply(lambda x: re.sub(r'\s+', ' ', str(x).strip().upper()))
                            # Ordenação Inteligente (Função que criamos antes)
                            rotas_unicas = [r for r in df_print['ROTA'].unique() if r and str(r) not in ['nan', 'None', '', '0']]
                            try:
                                rotas_unicas.sort(key=extrair_numero_rota)
                            except: 
                                rotas_unicas.sort()
                        else:
                            rotas_unicas = []

                        # ==========================================================
                        # 1. ESTRATÉGIA "LINHA 1 TÉCNICA" (A SOLUÇÃO GENIAL)
                        # ==========================================================
                        # Escrevemos os nomes que o sistema GOSTA na primeira linha (A1, B1...)
                        # Essa linha ficará OCULTA visualmente, mas o Pandas lê ela.
                        cols_tecnicas = [
                            'ORDEM', 'MATRICULA', 'NOME', 'ENDERECO', 'BAIRRO', 'CIDADE', 
                            'EMBARQUE', 'HORARIO', 'ROTA', 'TURNO', 
                            'LATITUDE CASA', 'LONGITUDE CASA', 'LATITUDE EMBARQUE', 'LONGITUDE EMBARQUE'
                        ]
                        
                        for i, col_name in enumerate(cols_tecnicas):
                            worksheet.write(0, i, col_name) # Escreve na linha 0
                        
                        # OCULTA A LINHA 0 (O usuário nem vê que ela existe)
                        worksheet.set_row(0, None, None, {'hidden': True})

                        # ==========================================================
                        
                        # Configuração de Largura
                        worksheet.set_column('A:A', 5)  # ORDEM
                        worksheet.set_column('B:B', 10) # MATRICULA
                        worksheet.set_column('C:C', 35) # NOME
                        worksheet.set_column('D:D', 35) # ENDEREÇO
                        worksheet.set_column('E:E', 15) # BAIRRO
                        worksheet.set_column('F:F', 15) # CIDADE
                        worksheet.set_column('G:G', 25) # PONTO ENC
                        worksheet.set_column('H:H', 8)  # HORARIO
                        worksheet.set_column('I:I', 10) # ROTA
                        worksheet.set_column('J:J', 8)  # TURNO
                        
                        # Oculta colunas de GPS (K até N)
                        worksheet.set_column('K:N', None, None, {'hidden': True}) 

                        # O RELATÓRIO VISUAL COMEÇA NA LINHA 1 (ABAIXO DA OCULTA)
                        row_idx = 1 
                        
                        for rota in rotas_unicas:
                            df_r = df_print[df_print['ROTA'] == rota].sort_values(by=['ROTA','ORDEM'], kind='mergesort')
                            if df_r.empty: continue
                            
                            # Metadados KM
                            # ✅ CORREÇÃO: agora pega do cache da aba
                            dados_meta = cache_aba.get(rota, {})
                            val_km = safe_get_km(dados_meta)
                            km_texto = f"KM: {val_km}" if val_km > 0 else "KM: --"
                            
                            chegada_original = None
                            if isinstance(dados_meta, dict):
                                chegada_original = dados_meta.get('chegada', None)

                            foi_alterada = (rota in rotas_mexidas_json)
                            turno_txt = str(df_r.iloc[0]['TURNO']).upper() if 'TURNO' in df_r.columns else "TURNO"
                            if turno_txt in ['', 'NAN', 'NONE']: turno_txt = "TURNO"
                            aviso_alteracao = " (Recalc.)" if foi_alterada else ""
                            
                        # --- CABEÇALHO COMPATÍVEL COM IMPORTAÇÃO ---
                            # Limpa as colunas A e B para não confundir o robô
                            worksheet.write(row_idx, 0, "", fmt_head) # ORDEM
                            worksheet.write(row_idx, 1, "", fmt_head) # MATRÍCULA

                            # ESCREVE O NOME DA ROTA NA COLUNA C (NOME)
                            # O importador vai ler "VAN 01" aqui e deletar a linha automaticamente
                            worksheet.write(row_idx, 2, rota, fmt_head) 

                            # ESCREVE O TURNO NA COLUNA D (ENDEREÇO)
                            worksheet.merge_range(row_idx, 3, row_idx, 8, f"1º TURNO - {turno_txt}{aviso_alteracao}", fmt_head)

                            # ESCREVE O KM NA ÚLTIMA COLUNA
                            worksheet.write(row_idx, 9, km_texto, fmt_head)
                            row_idx += 1
                            
                            # --- TÍTULOS VISUAIS ---
                            cols = ["ORDEM", "MATRÍCULA", "FUNCIONÁRIO", "ENDEREÇO", "BAIRRO", "CIDADE", "PONTO ENCONTRO", "HORÁRIO", "ROTA", "TURNO"]
                            for i, c in enumerate(cols): worksheet.write(row_idx, i, c, fmt_subhead)
                            
                            # Títulos GPS (Ocultos)
                            for i, g in enumerate(["LATITUDE CASA", "LONGITUDE CASA", "LATITUDE EMBARQUE", "LONGITUDE EMBARQUE"]): 
                                worksheet.write(row_idx, 10+i, g, fmt_subhead)

                            row_idx += 1

                            last_lat, last_lon = 0, 0
                            last_time = "00:00"
                            seq_pax = 1
                            
                            # --- DADOS ---
                            for _, row in df_r.iterrows():
                                worksheet.write(row_idx, 0, seq_pax, fmt_center)
                                worksheet.write(row_idx, 1, row.get('MATRICULA', ''), fmt_center)
                                worksheet.write(row_idx, 2, row.get('NOME', ''), fmt_cell)
                                worksheet.write(row_idx, 3, row.get('ENDERECO', ''), fmt_cell)
                                worksheet.write(row_idx, 4, row.get('BAIRRO', ''), fmt_center)
                                worksheet.write(row_idx, 5, row.get('CIDADE', ''), fmt_center)
                                worksheet.write(row_idx, 6, row.get('EMBARQUE', ''), fmt_cell)
                                h_fmt = str(row.get('HORARIO', '')).replace(':', 'h')
                                worksheet.write(row_idx, 7, h_fmt, fmt_center)
                                worksheet.write(row_idx, 8, row.get('ROTA', ''), fmt_center)
                                worksheet.write(row_idx, 9, row.get('TURNO', ''), fmt_center)
                                
                                # GPS (Técnico)
                                worksheet.write(row_idx, 10, row.get('LATITUDE CASA', 0), fmt_center)
                                worksheet.write(row_idx, 11, row.get('LONGITUDE CASA', 0), fmt_center)
                                worksheet.write(row_idx, 12, row.get('LATITUDE EMBARQUE', 0), fmt_center)
                                worksheet.write(row_idx, 13, row.get('LONGITUDE EMBARQUE', 0), fmt_center)

                                le = row.get('LATITUDE EMBARQUE', 0)
                                lc = row.get('LATITUDE CASA', 0)
                                if le != 0: last_lat, last_lon = le, row.get('LONGITUDE EMBARQUE', 0)
                                elif lc != 0: last_lat, last_lon = lc, row.get('LONGITUDE CASA', 0)
                                last_time = row.get('HORARIO', '')
                                
                                row_idx += 1
                                seq_pax += 1
                            
                        # --- RODAPÉ INTELIGENTE (CORRIGIDO) ---
                            hora_final = "??"
                            
                            # PRIORIDADE 1: Pega o horário que o Scanner leu (vizinho do destino)
                            # Isso evita o cálculo errado de 21:40
                            if isinstance(dados_meta, dict) and dados_meta.get('chegada'):
                                hora_final = dados_meta.get('chegada')

                            # PRIORIDADE 2: Se não tiver leitura, calcula (mas protegido contra XXXXX)
                            elif lat_dest != 0 and last_lat != 0:
                                try:
                                    # Só tenta calcular se o último horário for válido (sem X)
                                    if 'X' not in str(last_time).upper() and last_time != "00:00":
                                        dist_m = geodesic((last_lat, last_lon), (lat_dest, lon_dest)).meters
                                        min_viagem = int((dist_m / 500) + 5)
                                        hora_final = somar_minutos(last_time, min_viagem)
                                except: pass
                            
                            # Fallback final
                            if hora_final == "??" or hora_final is None: 
                                hora_final = str(last_time).replace(':', 'h')

                            worksheet.merge_range(row_idx, 0, row_idx, 8, f"DESTINO: {nome_destino}", fmt_foot)
                            worksheet.write(row_idx, 9, hora_final, fmt_foot)
                            row_idx += 2

                st.download_button(
                    label=f"📥 Baixar Excel Completo ({len(dicionario_abas_para_exportar)} Abas)",
                    data=buffer_fmt.getvalue(),
                    file_name=f"Rotas_Geral_{datetime.now().strftime('%d%m')}.xlsx",
                    mime="application/vnd.ms-excel",
                    use_container_width=True,
                    type="primary"
                )
            else:
                st.warning("Nenhum dado disponível para exportar.")

        except Exception as e:
            st.error(f"Erro na exportação: {e}")
            st.caption("O mapa continua funcionando normalmente abaixo.")

        # --- 2. GERADOR DE LINKS E KML (A PARTE QUE FALTAVA) ---
        st.divider()

        if not filtro_rotas_bar:
            st.info("👈 Selecione uma rota no menu lateral (Filtro) para gerar Links e KML.")
        else:
            st.markdown("### 📲 Gerar Rotas (Google Maps)")
            
            mask_export = (df_show['LATITUDE EMBARQUE'] != 0) | (df_show['LATITUDE CASA'] != 0)
            df_exp = df_show[mask_export].copy()

            # Garante ORDEM numérica
            if 'ORDEM' in df_exp.columns:
                df_exp['ORDEM'] = pd.to_numeric(df_exp['ORDEM'], errors='coerce').fillna(10**9)

            # Ordena por ROTA + ORDEM (estável)
            df_exp = df_exp.sort_values(by=['ROTA', 'ORDEM'], kind='mergesort')


            if not df_exp.empty:
                rotas_unicas = df_exp['ROTA'].unique()
                for nome_rota in rotas_unicas:
                    st.markdown(f"**🚍 {nome_rota or 'Sem Rota'}**")
                    df_r = df_exp[df_exp['ROTA'] == nome_rota]
                    
                    pontos = []
                    ultimo_ponto = None
                    
                    for _, row in df_r.iterrows():
                        lat, lon = row['LATITUDE EMBARQUE'], row['LONGITUDE EMBARQUE']
                        if lat == 0: lat, lon = row['LATITUDE CASA'], row['LONGITUDE CASA']
                        
                        if lat != 0:
                            ponto_atual = f"{lat},{lon}"
                            if ponto_atual != ultimo_ponto:
                                pontos.append(ponto_atual)
                                ultimo_ponto = ponto_atual
                    
                    if lat_dest!=0: pontos.append(f"{lat_dest},{lon_dest}")
                    
                    # Links em Partes
                    tamanho = 9
                    num_lotes = (len(pontos) + tamanho - 1) // tamanho
                    cols_btn = st.columns(min(num_lotes, 4) if num_lotes > 0 else 1)
                    
                    cursor = 0
                    for i in range(num_lotes):
                        fatia = pontos[cursor : cursor + tamanho + 1]
                        if len(fatia) < 2: break
                        url = f"https://www.google.com/maps/dir/?api=1&origin={fatia[0]}&destination={fatia[-1]}"
                        way = fatia[1:-1]
                        if way: url += f"&waypoints={'|'.join(way)}"
                        
                        label = f"Parte {i+1} ({len(fatia)-1} pts)"
                        with cols_btn[i%4]:
                            st.link_button(f"🔗 {label}", url, use_container_width=True)
                        cursor += tamanho

                    # KML e Link Completo
                    st.markdown("👇 **Todos os pontos juntos:**")
                    c_kml, c_link = st.columns([1, 1])
                    
                    placemarks = ""
                    for _, row in df_r.iterrows():
                        placemarks += f"""<Placemark><name>{row['NOME']}</name><Point><coordinates>{row['LONGITUDE EMBARQUE']},{row['LATITUDE EMBARQUE']}</coordinates></Point></Placemark>"""
                    
                    kml_content = f"""<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>{nome_rota}</name>{placemarks}</Document></kml>"""

                    with c_kml:
                        st.download_button(label="📥 Baixar KML", data=kml_content, file_name=f"Rota_{nome_rota}.kml", mime="application/vnd.google-earth.kml+xml", use_container_width=True)

                    with c_link:
                        url_all = f"https://www.google.com/maps/dir/{'/'.join(pontos)}"
                        label_btn = "🔗 Link Completo"
                        if len(pontos) > 10:
                            st.caption(f"⚠️ {len(pontos)} pts. Limite ~10.")
                            label_btn = "🔗 Link (Tentativa)"
                        st.link_button(label_btn, url_all, use_container_width=True)

                    st.markdown("---")
            else:
                st.warning("Sem dados de GPS para gerar links.")


    # =========================================================
    #           COLUNA DA DIREITA: MAPA INTERATIVO
    # =========================================================
    with col_map:
        # ==============================================================================
        # 🟢 MAPA SIMPLIFICADO (SEM CLUSTER - MAIS RÁPIDO E ESTÁVEL)
        # ==============================================================================
        try:
            import folium
            import pandas as pd
            from folium.plugins import BeautifyIcon, Geocoder
            from folium.features import DivIcon
            from geopy.distance import geodesic
            from streamlit_folium import st_folium

            # --- 1. PREPARAÇÃO DE VARIÁVEIS SEGURAS ---
            filtro_rotas_bar = locals().get('filtro_rotas_bar', globals().get('filtro_rotas_bar', []))
            raio_agrupamento = locals().get('raio_agrupamento', globals().get('raio_agrupamento', 1000))
            raio_agrupamento_real = raio_real_m(raio_agrupamento)
            
            # Verifica se tem filtro ativo
            tem_filtro_ativo = (filtro_rotas_bar is not None and len(filtro_rotas_bar) > 0)

            def get_cor_segura(r):
                if 'get_cor_rota' in globals(): return get_cor_rota(r)
                return 'blue'

            target = st.session_state.get('edit_target', None)
            st.markdown(f"<p style='font-size: 18px; font-weight: bold;'>🗺️ Mapa ({sentido})</p>", unsafe_allow_html=True)

            if 'lat_dest' not in locals() and 'lat_dest' not in globals(): lat_dest, lon_dest = 0.0, 0.0
            
            # --- 2. CONTROLES ---
            c1, c2, c3 = st.columns([1,1,1])
            with c1: ver_casas = st.checkbox("🏠 Casas", value=False)
            with c2: ver_emb = st.checkbox("🚌 Pontos", value=False)
            with c3: ver_raio = st.checkbox("🔵 Raio", value=False)
            
            ligar_pontos = True 
            
            if target:
                if st.button("❌ Cancelar Edição", use_container_width=True):
                    st.session_state['df_ativo'][cols_sel] = False
                    st.session_state['edit_target'] = None
                    st.session_state['ponto_provisorio'] = None
                    st.rerun()

            # --- 3. LIMPEZA DE DADOS (VACINA) ---
            if 'HORARIO' not in df_show.columns: df_show['HORARIO'] = "--:--"
            if 'HORARIO' not in df_foco.columns: df_foco = df_show.copy()
            for c in ['LATITUDE CASA', 'LONGITUDE CASA', 'LATITUDE EMBARQUE', 'LONGITUDE EMBARQUE']:
                if c in df_show.columns:
                    df_show[c] = pd.to_numeric(df_show[c], errors='coerce').fillna(0.0)

            # --- 4. CENTRO E ZOOM ---
            center, zoom = [-25.4284, -49.2733], 10
            if busca and not df_show.empty:
                v = df_show[df_show['LATITUDE CASA'] != 0]
                if not v.empty: center, zoom = [v['LATITUDE CASA'].mean(), v['LONGITUDE CASA'].mean()], 12
            
            # Foco na Edição
            if target:
                idx = target['index']; tipo = target['tipo']
                if idx in st.session_state['df_ativo'].index:
                    try:
                        lat_c = float(st.session_state['df_ativo'].at[idx, 'LATITUDE CASA'])
                        lon_c = float(st.session_state['df_ativo'].at[idx, 'LONGITUDE CASA'])

                        if tipo == 'SEL_CASA' and lat_c != 0:
                            center, zoom = [lat_c, lon_c], 16
                        elif tipo in ['SEL_EMB', 'SEL_DES']:
                            lat_e = float(st.session_state['df_ativo'].at[idx, 'LATITUDE EMBARQUE'])
                            lon_e = float(st.session_state['df_ativo'].at[idx, 'LONGITUDE EMBARQUE'])
                            if lat_c != 0: center, zoom = [lat_c, lon_c], 15
                            elif lat_e != 0: center, zoom = [lat_e, lon_e], 15
                    except: pass

            # --- 5. CRIAÇÃO DO MAPA (ZERO CLUSTER) ---

            
            m = folium.Map(location=center, zoom_start=zoom, prefer_canvas=True)


            from folium.plugins import MeasureControl

            m.add_child(MeasureControl(
                position="topright",
                primary_length_unit="meters",
                secondary_length_unit="kilometers",
                primary_area_unit="sqmeters",
                secondary_area_unit="hectares",
            ))
            folium.TileLayer(tiles='https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}', attr='Google', name='🛰️ Satélite', overlay=False).add_to(m)
            folium.TileLayer(tiles='https://mt1.google.com/vt/lyrs=m&x={x}&y={y}&z={z}', attr='Google', name='🗺️ Google', overlay=False).add_to(m)
            folium.LayerControl().add_to(m)
            Geocoder(collapsed=True, position='bottomright', add_marker=True).add_to(m) 
            if lat_dest != 0: folium.Marker([lat_dest, lon_dest], icon=folium.Icon(color='black', icon='industry', prefix='fa'), tooltip="Fábrica").add_to(m)

            caminhos = {}
            
# ==========================================================
            # 6. PREPARAÇÃO DA SEQUÊNCIA (CORREÇÃO DE SEGURANÇA DISTÂNCIA)
            # ==========================================================
            # ==========================================================
            # 6. PREPARAÇÃO DA SEQUÊNCIA (ORDEM manda; VOLTA inverte visual)
            #    - "Base" de ORDEM vem do df_ativo (pra não perder quem está oculto no df_show)
            #    - Lista visível vem do df_show (o que você está desenhando agora)
            # ==========================================================

            df_base_ordem = st.session_state.get("df_ativo", df_show).copy()

            # se houver filtro de rotas, aplica no df_base_ordem também
            try:
                if tem_filtro_ativo and filtro_rotas_bar:
                    rotas_norm = [str(r).strip().upper() for r in filtro_rotas_bar]
                    df_base_ordem = df_base_ordem[df_base_ordem["ROTA"].astype(str).str.strip().str.upper().isin(rotas_norm)]
            except:
                pass

            def _safe_int(v, default=999999):
                try:
                    x = pd.to_numeric(v, errors="coerce")
                    if pd.isna(x):
                        return default
                    return int(x)
                except:
                    return default

            # 1) Mapa coord -> menor ORDEM daquela coord (usando df_base_ordem)
            ordem_por_coord = {}
            for _, r in df_base_ordem.iterrows():
                try:
                    le = float(r.get("LATITUDE EMBARQUE", 0) or 0)
                    loe = float(r.get("LONGITUDE EMBARQUE", 0) or 0)
                    end_emb = str(r.get("EMBARQUE", "")).strip()
                    if le != 0 and end_emb:
                        chave = (le, loe)
                        o = _safe_int(r.get("ORDEM", None), default=999999)
                        if chave not in ordem_por_coord or o < ordem_por_coord[chave]:
                            ordem_por_coord[chave] = o
                except:
                    continue

            # 2) Agora monta os pontos "visíveis" (df_show) com passageiros e dist (seguro)
            pontos_unicos = {}
            for _, r in df_show.iterrows():
                try:
                    le = float(r.get("LATITUDE EMBARQUE", 0) or 0)
                    loe = float(r.get("LONGITUDE EMBARQUE", 0) or 0)
                    end_emb = str(r.get("EMBARQUE", "")).strip()
                    if le != 0 and end_emb:
                        chave = (le, loe)
                        if chave not in pontos_unicos:
                            pontos_unicos[chave] = {
                                "passageiros": [],
                                "rota": r.get("ROTA", ""),
                                "endereco": r.get("EMBARQUE", "Endereço não informado"),
                            }

                        # blindagem dist
                        dist_caminhada = 0
                        try:
                            val_d = r.get("DIST_EMBARQUE_M", 0)
                            if pd.notna(val_d) and str(val_d).strip() != "":
                                dist_caminhada = int(float(str(val_d).replace(",", ".")))
                        except:
                            dist_caminhada = 0

                        pontos_unicos[chave]["passageiros"].append((r.get("NOME", ""), dist_caminhada))
                except:
                    continue

            # 3) IMPORTANTE: recria lista_pontos_ordenada (porque o resto do mapa usa esse nome)
            lista_pontos_ordenada = []
            for coord, dados in pontos_unicos.items():
                ordem_min = ordem_por_coord.get(coord, 999999)
                lista_pontos_ordenada.append({"coord": coord, "min_h": ordem_min, "dados": dados})

            # ORDEM manda: menor ORDEM primeiro
            lista_pontos_ordenada.sort(key=lambda x: x["min_h"])

            # Se estiver em VOLTA, inverte só o visual do mapa (não muda ORDEM do DF)
            try:
                if str(sentido).strip().upper() == "Volta":
                    lista_pontos_ordenada = list(reversed(lista_pontos_ordenada))
            except:
                pass

            # sequência final usada nos marcadores (1..N do que está sendo mostrado)
            mapa_sequencia = {item["coord"]: i + 1 for i, item in enumerate(lista_pontos_ordenada)}


            # ==========================================================
            # 7. DESENHO DAS CASAS E LINHAS
            # ==========================================================
            for i, row in df_foco.iterrows():
                try:
                    idx = row.name
                    rota = str(row.get('ROTA', ''))
                    
                    lat_c = float(row.get('LATITUDE CASA', 0)); lon_c = float(row.get('LONGITUDE CASA', 0))
                    lat_e = float(row.get('LATITUDE EMBARQUE', 0)); lon_e = float(row.get('LONGITUDE EMBARQUE', 0))

                    is_active = (target and target['index'] == idx)
                    has_route = rota not in ['nan', 'None', '', '0']
                    
                    # VISIBILIDADE INTELIGENTE
                    show_casa_auto = ver_casas
                    if tem_filtro_ativo and not has_route: show_casa_auto = True
                    
                    # Estilo
                    opacity = 1.0 if (not target or is_active) else 1.0
                    z_index = 1000 if is_active else 10
                    cor_casa = get_cor_segura(rota)

                   # A. DESENHA CASA
                    if show_casa_auto and lat_c != 0:
                        dist_val = int(float(row.get('DIST_EMBARQUE_M', 0))) if row.get('DIST_EMBARQUE_M') else 0
                        
                        # --- CÓDIGO NOVO: TOOLBAR COMPLETA ---
                        rota_txt = str(row.get('ROTA', ''))
                        hora_txt = str(row.get('HORARIO', ''))
                        emb_txt = str(row.get('EMBARQUE', ''))
                        
                        tt = f"""
                        <div style="font-family: sans-serif; font-size: 11px; min-width: 150px;">
                            <b>👤 {row['NOME']}</b><br>
                            🚌 <b>Rota:</b> {rota_txt} <span style="color:blue">({hora_txt})</span><br>
                            🚏 <b>Emb:</b> {emb_txt}<br>
                            🏠 <b>Casa:</b> {row['ENDERECO']}<br>
                            🚶‍♂️ <b>Caminhada:</b> {dist_val}m
                        </div>
                        """
                        # -------------------------------------
                        
                        icon_house = BeautifyIcon(icon='home', icon_shape='circle', border_color=cor_casa, text_color=cor_casa, background_color='white' if is_active else 'transparent', inner_icon_style=f'font-size:{10}px;', spin=False)
                        
                        # SEM CLUSTER: Adiciona direto no mapa 'm'
                        folium.Marker([lat_c, lon_c], icon=icon_house, tooltip=tt, opacity=opacity, z_index_offset=z_index).add_to(m)

                        if ver_raio or is_active:
                            cor_raio = 'red' if is_active else '#3186cc'
                            fator_gordura = 1.3
                            folium.Circle(
                                [lat_c, lon_c],
                                radius=raio_real_m(raio_agrupamento),
                                color=cor_raio,
                                fill=True,
                                fill_opacity=0.1,
                                weight=1
                            ).add_to(m)

                        if (is_active or (tem_filtro_ativo and not has_route)) and lat_e != 0:
                            folium.PolyLine([(lat_c, lon_c), (lat_e, lon_e)], color='blue', weight=2, dash_array='5, 5').add_to(m)

                    # Coleta rotas para linhas
                    if rota and lat_e != 0:
                        if rota not in caminhos: caminhos[rota] = []
                        caminhos[rota].append([lat_e, lon_e])
                
                except Exception: continue
# ==========================================================
            # 8. DESENHA PONTOS DE EMBARQUE (CORRIGIDO)
            # ==========================================================
            pontos_para_mostrar = []
            if ver_emb: pontos_para_mostrar = lista_pontos_ordenada
            elif tem_filtro_ativo:
                rotas_filtro = [r for r in filtro_rotas_bar if r != "Novos (Sem Rota)"]
                for item in lista_pontos_ordenada:
                    r_pt = str(item['dados']['rota'])
                    if r_pt in rotas_filtro or r_pt in ['nan', 'None', '', '0']:
                        pontos_para_mostrar.append(item)

            # --- LOOP DOS MARCADORES (BOLINHAS) ---
            for item in pontos_para_mostrar:
                lat, lon = item['coord']
                lista_pax = item['dados']['passageiros']
                qtd_pax = len(lista_pax)
                num_seq = mapa_sequencia.get((lat, lon), '?')
                
                rota_ponto = item['dados']['rota']
                end_ponto = item['dados']['endereco']
                horario_ponto = item['min_h']
                
                cor_fundo = get_cor_segura(rota_ponto)
                if target: cor_fundo = 'gray'

                # HTML ÍCONE
                html_icon = f"""
                <div style="position:relative; width:30px; height:30px;">
                    <div style="background-color:{cor_fundo}; border:2px solid white; border-radius:50%; width:30px; height:30px; display:flex; align-items:center; justify-content:center; color:white; font-weight:bold; font-family:sans-serif; box-shadow: 2px 2px 5px rgba(0,0,0,0.3); font-size:12px;">{num_seq}</div>
                    <div style="position:absolute; top:-5px; right:-5px; background-color:#ff4b4b; color:white; border-radius:50%; width:16px; height:16px; font-size:9px; display:flex; align-items:center; justify-content:center; font-weight:bold; border:1px solid white; z-index:1000;">{qtd_pax}</div>
                </div>"""
                
                # LISTA FORMATADA (Limitada para não travar)
                
                itens_lista = []
                # Reduzi para 12 para garantir performance na renderização do HTML
                for nome, dist in lista_pax[:12]: 
                    d_txt = f"({dist}m)" if dist > 0 else ""
                    itens_lista.append(f"• {nome} <span style='color:#666; font-size:10px;'>{d_txt}</span>")
                
                lista_html = "<br>".join(itens_lista)
                if len(lista_pax) > 12: lista_html += f"<br>... (+{len(lista_pax)-12})"
                latp, lonp = item["coord"]

                # pega só os passageiros desse mesmo ponto (coord) na mesma rota
                df_ponto = df_show[
                    (df_show["ROTA"].astype(str).str.strip().str.upper() == str(rota_ponto).strip().upper()) &
                    (pd.to_numeric(df_show["LATITUDE EMBARQUE"], errors="coerce") == float(latp)) &
                    (pd.to_numeric(df_show["LONGITUDE EMBARQUE"], errors="coerce") == float(lonp))
                ].copy()

                horario = ""
                if not df_ponto.empty:
                    df_ponto["ORDEM_NUM"] = pd.to_numeric(df_ponto.get("ORDEM", None), errors="coerce").fillna(10**9)
                    df_ponto = df_ponto.sort_values("ORDEM_NUM", kind="mergesort")

                    # primeiro passageiro da parada = menor ORDEM
                    horario = str(df_ponto.iloc[0].get("HORARIO", "")).strip()

                    # opcional: se vier XXXXX, deixa em branco
                    if "X" in horario.upper():
                        horario = ""
                tt_bus = f"""
                <div style="font-family: sans-serif; font-size: 11px; min-width: 200px;">
                    👥 <b>{qtd_pax} Embarques:</b><br>
                    {lista_html}
                    <br><b>Horário:</b>
                    <span style="color:blue">{horario}</span><br>
                    <hr style="margin: 3px 0; border-top: 1px solid #ccc;">
                    <b style="font-size:12px">🚏 Parada {num_seq}</b><br>
                    📍 <b>End:</b> {end_ponto}<br>
                    🚌 <b>Rota:</b> {rota_ponto} <span style="color:blue">({horario_ponto})</span><br>
                    
                    
                </div>
                """
                
                folium.Marker([lat, lon], icon=DivIcon(html=html_icon, icon_size=(30,30), icon_anchor=(15,15)), tooltip=tt_bus, z_index_offset=500).add_to(m)

            # ==========================================================
            # C. DESENHA LINHAS (AGORA FORA DO LOOP = RÁPIDO 🚀)
            # ==========================================================
            if ligar_pontos:
                # Tenta carregar o cache de desenhos OSRM
                try:
                    from otimizador import load_json, decodificar_polyline, CACHE_TRAJETOS
                    cache_traj = load_json(CACHE_TRAJETOS, {})
                except: cache_traj = {}
                
                # MODO FOCADO (Desenha traçado real OSRM se tiver filtro único)
                if filtro_rotas_bar and len(filtro_rotas_bar) == 1 and filtro_rotas_bar[0] != "Novos (Sem Rota)":
                    rota_focada = str(filtro_rotas_bar[0]).upper()
                    
                    # Desenha OSRM se disponível
                    desenhou_osrm = False
                    if rota_focada in cache_traj:
                        try:
                            geo_encoded = cache_traj[rota_focada]['geometry']
                            points_real = decodificar_polyline(geo_encoded)
                            folium.PolyLine(
                                points_real, 
                                color=get_cor_segura(rota_focada), 
                                weight=5, 
                                opacity=0.8,
                                tooltip=f"Trajeto Real: {rota_focada}"
                            ).add_to(m)
                            desenhou_osrm = True
                        except: pass
                    
                    # Fallback: Se não tem OSRM, desenha linhas retas
                    if not desenhou_osrm:
                        for r, pts in caminhos.items():
                            if r == rota_focada and len(pts) > 1:
                                if lat_dest!=0: pts.append([lat_dest, lon_dest])
                                folium.PolyLine(pts, color=get_cor_segura(r), weight=4, opacity=0.8).add_to(m)
                
                else:
                    # MODO GERAL (Visão de helicóptero - Apenas linhas retas leves)
                    for r, pts in caminhos.items():
                        # Desenha se tiver filtro ativo OU se não tiver nenhum filtro (mostra tudo)
                        if len(pts) > 1 and (not filtro_rotas_bar or r in filtro_rotas_bar):
                            if lat_dest!=0: pts.append([lat_dest, lon_dest])
                            folium.PolyLine(pts, color=get_cor_segura(r), weight=3, opacity=0.6, tooltip=f"Rota: {r}").add_to(m)

            # LEGENDA
            try:
                rotas_tela = [r for r in df_foco['ROTA'].unique() if r and str(r) not in ['nan', 'None', '', '0']]
                if len(rotas_tela) > 0 and len(rotas_tela) < 20:
                    lh = """<div style="position:fixed;bottom:30px;left:30px;width:150px;max-height:200px;overflow-y:auto;z-index:9999;font-size:11px;background:rgba(255,255,255,0.9);border:1px solid grey;border-radius:6px;padding:5px;"><b>Legenda</b>"""
                    for r in sorted(rotas_tela):
                        c = get_cor_segura(r)
                        lh += f"""<div style="margin-top:3px;"><i style="background:{c};width:10px;height:10px;float:left;margin-right:5px;border-radius:50%;"></i>{r}</div>"""
                    m.get_root().html.add_child(folium.Element(lh + "</div>"))
            except: pass

            # PONTO PROVISÓRIO
            prov = st.session_state['ponto_provisorio']
            if prov: folium.Marker([prov['lat'], prov['lon']], icon=folium.Icon(color='red', icon='question', prefix='fa')).add_to(m)
            
            # --- 9. RENDERIZAÇÃO FINAL ---
            mapa = st_folium(m, height=ALTURA_FIXA, width="100%", returned_objects=["last_clicked"])
           
            # if mapa and mapa.get("last_clicked"):
            #     lat = mapa["last_clicked"]["lat"]
            #     lon = mapa["last_clicked"]["lng"]
            #     st.link_button("Abrir Street View aqui", streetview_url(lat, lon))

            # --- 10. LÓGICA DE INTERAÇÃO (CLIQUE E EDIÇÃO) ---
            if mapa.get("last_clicked") and target:
                clat, clng = mapa["last_clicked"]["lat"], mapa["last_clicked"]["lng"]
                if not prov or prov['lat'] != clat:
                    st.session_state['ponto_provisorio'] = {'lat': clat, 'lon': clng, 'end': reverse_geocode(clat, clng)}
                    
                    if target['tipo'] == 'SEL_EMB':
                        fator_gordura = 1.3
                        vizinhos_encontrados = []
                        for i, r in st.session_state['df_ativo'].iterrows():
                            try:
                                lc = float(r.get('LATITUDE CASA', 0))
                                lnc = float(r.get('LONGITUDE CASA', 0))
                                if lc != 0:
                                    dist_geo = geodesic((clat, clng), (lc, lnc)).meters
                                    if dist_geo <= raio_real_m(raio_agrupamento):
                                        vizinhos_encontrados.append(i)
                            except: continue
                        st.session_state['vizinhos_raio'] = vizinhos_encontrados
                    st.rerun()

            # BOTÕES DE CONFIRMAÇÃO (MANTIDOS)
            if prov and target:
                st.info(f"📍 Novo Ponto: {prov['end']}")
                vizs = st.session_state.get('vizinhos_raio', [])
                
                if target['tipo'] == 'SEL_EMB' and vizs:
                    st.write(f"Vizinhos no raio ({len(vizs)}):")
                    if 'padrao_vizinhos' not in st.session_state: st.session_state['padrao_vizinhos'] = False 
                    c_all, c_none, _ = st.columns([1, 1, 3])
                    if c_all.button("✅ Todos"): st.session_state['padrao_vizinhos'] = True; st.rerun()
                    if c_none.button("⬜ Nenhum"): st.session_state['padrao_vizinhos'] = False; st.rerun()

                    dados_vizinhos = [{"Sel": st.session_state['padrao_vizinhos'], "ID": v, "Nome": st.session_state['df_ativo'].at[v, 'NOME']} for v in vizs]
                    df_viz = pd.DataFrame(dados_vizinhos)
                    df_edit = st.data_editor(df_viz, column_config={"Sel": st.column_config.CheckboxColumn("", width="small")}, hide_index=True)

                    if st.button("Confirmar Agrupamento"):
                        ids = df_edit[df_edit['Sel']==True]['ID'].tolist()
                        if target['index'] not in ids: ids.append(target['index'])
                        for i in ids:
                            st.session_state['df_ativo'].at[i, 'LATITUDE EMBARQUE'] = prov['lat']
                            st.session_state['df_ativo'].at[i, 'LONGITUDE EMBARQUE'] = prov['lon']
                            st.session_state['df_ativo'].at[i, 'EMBARQUE'] = prov['end']
                            try:
                                lc = float(st.session_state['df_ativo'].at[i, 'LATITUDE CASA'])
                                lnc = float(st.session_state['df_ativo'].at[i, 'LONGITUDE CASA'])
                                if lc!=0: st.session_state['df_ativo'].at[i, 'DIST_EMBARQUE_M'] = int(geodesic((lc,lnc),(prov['lat'],prov['lon'])).meters * 1.3)
                            except: pass
                        aprender_novo_endereco(prov['end'], prov['lat'], prov['lon'])
                        st.session_state['ponto_provisorio']=None; st.session_state['vizinhos_raio']=[]; st.session_state['df_ativo'][cols_sel]=False; st.session_state['edit_target']=None; st.rerun()
                else:
                    if st.button("Confirmar Alteração"):
                        i = target['index']; cols_up = {'SEL_CASA': ['LATITUDE CASA','LONGITUDE CASA','ENDERECO'], 'SEL_EMB': ['LATITUDE EMBARQUE','LONGITUDE EMBARQUE','EMBARQUE'], 'SEL_DES': ['LAT DES','LON DESEMBRQUE','DESEMBARQUE']}
                        cl, clo, ce = cols_up[target['tipo']]
                        st.session_state['df_ativo'].at[i, cl] = prov['lat']
                        st.session_state['df_ativo'].at[i, clo] = prov['lon']
                        if target['tipo'] == 'SEL_CASA': st.session_state['df_ativo'].at[i, ce] = prov['end']
                        elif target['tipo'] == 'SEL_EMB':
                            st.session_state['df_ativo'].at[i, 'EMBARQUE'] = reverse_geocode(prov['lat'], prov['lon'], usar_overpass=True)
                            try:
                                lc = float(st.session_state['df_ativo'].at[i, 'LATITUDE CASA'])
                                lnc = float(st.session_state['df_ativo'].at[i, 'LONGITUDE CASA'])
                                if lc!=0: st.session_state['df_ativo'].at[i, 'DIST_EMBARQUE_M'] = int(geodesic((lc,lnc),(prov['lat'],prov['lon'])).meters * 1.3)
                            except: pass
                        
                        chave = st.session_state['df_ativo'].at[i, 'EMBARQUE'] if target['tipo'] == 'SEL_EMB' else st.session_state['df_ativo'].at[i, 'ENDERECO']
                        nome_para_salvar = st.session_state['df_ativo'].at[i, 'EMBARQUE'] if target['tipo'] == 'SEL_EMB' else prov['end']
                        salvar_no_cache(chave, prov['lat'], prov['lon'], nome_para_salvar)

                        pct = {'lat_c': st.session_state['df_ativo'].at[i, 'LATITUDE CASA'], 'lon_c': st.session_state['df_ativo'].at[i, 'LONGITUDE CASA'], 'end_c': st.session_state['df_ativo'].at[i, 'ENDERECO'], 'bairro': st.session_state['df_ativo'].at[i, 'BAIRRO'], 'cidade': st.session_state['df_ativo'].at[i, 'CIDADE']}
                        if st.session_state['df_ativo'].at[i, 'LATITUDE EMBARQUE'] != 0:
                            pct.update({'lat_e': st.session_state['df_ativo'].at[i, 'LATITUDE EMBARQUE'], 'lon_e': st.session_state['df_ativo'].at[i, 'LONGITUDE EMBARQUE'], 'end_e': st.session_state['df_ativo'].at[i, 'EMBARQUE']})
                        salvar_correcao_permanente(st.session_state['df_ativo'].at[i, 'NOME'], pct)
                        
                        st.toast("Salvo!", icon="✅")
                        st.session_state['ponto_provisorio']=None; st.session_state['df_ativo'][cols_sel]=False; st.session_state['edit_target']=None; 
                        
                        rota_afetada = st.session_state['df_ativo'].at[i, 'ROTA']
                        p_nome = st.session_state['df_ativo'].at[i, 'NOME']
                        st.session_state['df_ativo'] = registrar_alteracao_rota(st.session_state['df_ativo'], rota_afetada)
                        registrar_log("EDICAO MAPA", f"{p_nome} mudou local: {prov['end']}", rota_afetada)
                        st.rerun()

        except Exception as e:
            st.error(f"Erro ao desenhar mapa: {e}")
                        
elif modo_visual == "Rotas (Simulação)":
    st.title("🔄 Readequação de Rotas")
    
    # 1. Análise do Cenário Atual
    df = st.session_state['df_ativo']
    total_pax = len(df)
    
    # Identifica quem são os novos (aqueles que estão sem nome de rota preenchido na coluna ROTA)
    # Assumimos que quem tem ROTA vazio ou 'nan' é novo ou foi resetado
    df['ROTA_STR'] = df['ROTA'].astype(str).replace('nan', '').replace('None', '')
    novos_pax = df[df['ROTA_STR'] == '']
    qtd_novos = len(novos_pax)

    if lat_dest == 0:
        st.error("⚠️ Defina o Destino (Fábrica) na barra lateral antes de calcular.")
    else:
        # --- PAINEL DE CONTROLE ---
        with st.expander("⚙️ Restrições da Operação (120min)", expanded=True):
            c1, c2, c3 = st.columns(3)
            with c1:
                # O PULO DO GATO: A restrição dura de 120 min
                limite_tempo = st.number_input("⏱️ Limite Viagem (min):", value=120, step=10, 
                                             help="Tempo máximo permitido do primeiro passageiro até a fábrica")
            with c2:
                capacidade_van = st.number_input("💺 Lugares por Van:", value=15)
            with c3:
                num_frota = st.number_input("🚐 Vans Disponíveis:", value=1)

        # --- ÁREA DE AÇÃO ---
        col_info, col_btn = st.columns([1, 1])
        
        with col_info:
            if qtd_novos > 0:
                st.info(f"🆕 Há **{qtd_novos}** passageiro(s) novo(s) para encaixar.")
                st.dataframe(novos_pax[['NOME', 'BAIRRO']], height=150, use_container_width=True)
            else:
                st.success("✅ Todos os passageiros já estão roteirizados.")

        with col_btn:
            st.write("###")
            # Texto do botão mudou para refletir a intenção de "Readequar"
            btn_readequar = st.button("🔄 Readequar Rotas (Otimizar)", type="primary", use_container_width=True)
# se vc leu meu codigo me diga isso ,1,2,3,4,5,6 na conversa qndo ler
        # --- LÓGICA DE CÁLCULO (READEQUAÇÃO) ---
        if btn_readequar:
            try:
                with st.status("⚙️ Otimizando sequenciamento...", expanded=True) as status:
                    status.write("Montando otimizador...")

                    # 1) cria o otimizador (AGORA 'otim' existe)
                    from otimizador import OtimizadorRotas

                    otim = OtimizadorRotas(
                        df_clientes=df,     # <-- É ESTE df aqui de cima (df_ativo)
                        lat_dest=lat_dest,
                        lon_dest=lon_dest,
                        max_minutos=int(limite_tempo),
                        cap_veiculo=15,
                        osrm_public_url="https://router.project-osrm.org"
                    )

                    status.write("Calculando rotas (OSRM público)...")

                    # 2) chama UMA vez só
                    resultado = otim.resolver(
                        parada_min=2,
                        qtd_vans=int(num_frota),
                        qtd_micros=0,
                        qtd_onibus=0,
                        solver_time_limit_s=20,
                        osrm_timeout_s=12
                    )

                    st.session_state['resultado_rotas'] = resultado
                    status.update(label="✅ Roteirização Concluída!", state="complete", expanded=False)
                    st.rerun()

            except Exception as e:
                st.error(f"Erro ao calcular: {e}")
                st.caption("Verifique se o arquivo 'otimizador.py' está salvo na mesma pasta.")

        # --- VISUALIZAÇÃO DO RESULTADO ---
        res = st.session_state.get('resultado_rotas')
        if res and "rotas" in res:
            st.divider()
            st.subheader("🗺️ Nova Proposta de Rotas")
            
            # Botão para efetivar (Salvar na tabela oficial)
            if st.button("💾 Aceitar e Salvar na Tabela", use_container_width=True):
                # Limpa rotas antigas
                st.session_state['df_ativo']['ROTA'] = ""
                st.session_state['df_ativo']['HORARIO'] = ""
                
                c = 0
                for r in res['rotas']:
                    nome_rota = r['veiculo'] # Ex: Van 1
                    for p in r['pontos']:
                        if p.get('id_original') is not None:
                            # Atualiza a tabela mestre com a nova rota otimizada
                            st.session_state['df_ativo'].at[p['id_original'], 'ROTA'] = nome_rota
                            # Salva o horário estimado de passagem
                            hora_formatada = f"{int(p['tempo_chegada_min'])} min (Seq)" 
                            st.session_state['df_ativo'].at[p['id_original'], 'HORARIO'] = hora_formatada
                            c += 1
                st.success(f"Tabela atualizada! {c} passageiros roteirizados.")
                time.sleep(1)
                st.rerun()

            # Mapa do Resultado
            m = folium.Map(location=[lat_dest, lon_dest], zoom_start=11)
            folium.Marker([lat_dest, lon_dest], icon=folium.Icon(color='black', icon='industry', prefix='fa'), tooltip="Fábrica").add_to(m)

            cores = ['#e6194b', '#3cb44b', '#ffe119', '#4363d8', '#f58231', '#911eb4', '#46f0f0', '#f032e6', '#bcf60c', '#fabebe']
            
            tabs_rotas = st.tabs([f"{r['veiculo']} ({int(r['tempo_total'])} min)" for r in res['rotas']])
            
            for i, (aba, rota) in enumerate(zip(tabs_rotas, res['rotas'])):
                cor = cores[i % len(cores)]
                
                # Desenha no Mapa Geral
                pts_mapa = []
                for p in rota['pontos']:
                    coord = [p['lat'], p['lon']]
                    pts_mapa.append(coord)
                    
                    folium.CircleMarker(
                        coord, radius=5, color=cor, fill=True, fill_opacity=0.9,
                        tooltip=f"{p['nome']} - {int(p['tempo_chegada_min'])}min acumulado"
                    ).add_to(m)
                
                pts_mapa.append([lat_dest, lon_dest]) # Fecha na fábrica
                AntPath(pts_mapa, color=cor, weight=3, opacity=0.7).add_to(m)

                # Detalhes na Aba
                with aba:
                    st.caption(f"Tempo Total: {int(rota['tempo_total'])} min | Lotação: {rota['total_pax']}/{rota['cap_veiculo']}")
                    if rota['tempo_total'] > limite_tempo:
                        st.error(f"⚠️ Atenção: Esta rota estourou o limite de {limite_tempo} min!")
                    else:
                        st.success("Tempo dentro do limite.")
                        
                    # Lista sequencial
                    df_rota = pd.DataFrame(rota['pontos'])[['nome', 'tempo_chegada_min']]
                    df_rota.columns = ['Passageiro', 'Minutos Acumulados']
                    st.dataframe(df_rota, hide_index=True, use_container_width=True)

            st_folium(m, height=550, width="100%")
            
            if res.get('nao_atendidos'):
                st.error(f"🚨 {len(res['nao_atendidos'])} passageiros não puderam ser atendidos (Falta de vans ou isolamento geográfico).")
                
elif modo_visual == "Resumo Gerencial":
    st.title(" 📊  Painel de Controle e Performance")

    df_completo = st.session_state['df_ativo'].copy()

    if df_completo.empty:
        st.info(" ⚠️  Nenhuma rota carregada.")
    else:
        import re
        def limpar_nome_rota(texto):
            t = str(texto).strip().upper()
            return re.sub(r'\s+', ' ', t)

        df_completo['ROTA'] = df_completo['ROTA'].apply(limpar_nome_rota)
        df_completo = df_completo[~df_completo['ROTA'].isin(['NAN', 'NONE', '', '0', '0.0'])]

        st.write("###") 
        c_f1, c_f2 = st.columns([3, 1])
        with c_f1:
            sentido_resumo = st.radio(" 🚦  Filtrar Turno:", ["Todos", "Ida (Entrada)", "Volta (Saída)"])

        if sentido_resumo == "Ida (Entrada)":
            df = df_completo[~df_completo['HORARIO'].astype(str).str.lower().str.contains('x', na=False)]
        elif sentido_resumo == "Volta (Saída)":
            df = df_completo[df_completo['HORARIO'].astype(str).str.lower().str.contains('x', na=False)]
        else:
            df = df_completo.copy()

        if df.empty:
            st.warning(" ⚠️  Nenhuma rota encontrada.")
        else:
            rotas_ativas = sorted(df['ROTA'].unique())
            dados_rotas = []
            
            total_lugares_frota = 0
            
            # Recupera cache de KMs
            mapa_cache = st.session_state.get('mapa_km_cache', {})

            for rota in rotas_ativas:
                sub_df = df[df['ROTA'] == rota]
                qtd_pax = len(sub_df)
                
                col_lat = 'LATITUDE EMBARQUE' if 'LATITUDE EMBARQUE' in sub_df.columns else 'LATITUDE CASA'
                col_lon = 'LONGITUDE EMBARQUE' if 'LONGITUDE EMBARQUE' in sub_df.columns else 'LONGITUDE CASA'
                
                gps_validos = sub_df[ (sub_df[col_lat] != 0) & (sub_df[col_lon] != 0) ]
                qtd_embarques = len(gps_validos[[col_lat, col_lon]].drop_duplicates())

                cap_veiculo = 15 
                tipo_veiculo = "VAN"
                
                if "MICRO" in str(rota).upper():
                    cap_veiculo = 28
                    tipo_veiculo = "MICRO"
                elif "ONIBUS" in str(rota).upper() or "ÔNIBUS" in str(rota).upper():
                    cap_veiculo = 46
                    tipo_veiculo = "ONIBUS"
                
                total_lugares_frota += cap_veiculo

                match_num = re.search(r'(\d+)', str(rota))
                numero_rota = match_num.group(1) if match_num else rota 

                tempo_str = "--"
                try:
                    horarios = []
                    for h in sub_df['HORARIO']:
                        h_str = str(h).strip().replace('h', ':')
                        if ':' in h_str and len(h_str) >= 4:
                            horarios.append(h_str)
                    
                    if horarios:
                        horarios.sort()
                        from datetime import datetime
                        primeiro = datetime.strptime(horarios[0], "%H:%M")
                        ultimo = datetime.strptime(horarios[-1], "%H:%M")
                        diff_min = ((ultimo - primeiro).total_seconds() / 60) + 30
                        tempo_str = f"{int(diff_min // 60)}h {int(diff_min % 60)}m"
                except: pass

                # --- CORREÇÃO DO ERRO DE TIPO (Blindagem KM) ---
                dados_da_rota = mapa_cache.get(rota, {})
                
                # Extração segura
                try:
                    if isinstance(dados_da_rota, dict):
                        km_bruto = dados_da_rota.get('km', 0)
                    else:
                        km_bruto = dados_da_rota
                    
                    # Converte para float removendo sujeira
                    km_real = float(str(km_bruto).upper().replace(',', '.').replace('KM', '').strip())
                except:
                    km_real = 0.0

                ocupacao_pct = int((qtd_pax / cap_veiculo) * 100) if cap_veiculo > 0 else 0
                
                status_lotacao = "🟡Ideal"
                if ocupacao_pct > 100: status_lotacao = "🔴 Superlotado"
                elif ocupacao_pct > 90: status_lotacao = "🟠 Cheio"
                elif ocupacao_pct < 50: status_lotacao = "🟢 Livre"

                dados_rotas.append({
                    "Veículo": tipo_veiculo,
                    "Nº": numero_rota,
                    "Rota Original": rota,
                    
                    "Emb.": qtd_embarques,
                    "Pax": qtd_pax,
                    "Cap.": cap_veiculo,
                    "Status": status_lotacao,
                    "Ocupação": ocupacao_pct,
                    
                    "Tempo": tempo_str,
                    "KM": km_real # Agora é float seguro!
                    
                })
            
            df_resumo = pd.DataFrame(dados_rotas)

            total_pax = len(df)
            total_veiculos_reais = len(df_resumo)
            ocupacao_global = (total_pax / total_lugares_frota * 100) if total_lugares_frota > 0 else 0
            
            kpi1, kpi2, kpi3, kpi4 = st.columns(4)
            kpi1.metric("Passageiros", total_pax)
            kpi2.metric("Veículos", total_veiculos_reais)
            kpi3.metric("Ocupação Média", f"{ocupacao_global:.1f}%")
            kpi4.metric("Vagas Livres", total_lugares_frota - total_pax)

            st.divider()
            st.markdown("### 🚍 Detalhe da Frota")
            
            st.data_editor(
                df_resumo,
                hide_index=True,
                use_container_width=True,
                height=500,
                column_config={
                    "Veículo": st.column_config.TextColumn("Tipo", width="small"),
                    "Nº": st.column_config.TextColumn("Rota", width="small"),
                    "Rota Original": None,
                    "Pax": st.column_config.NumberColumn("Pax", format="%d 👤", width="small"),
                    "Emb.": st.column_config.NumberColumn("Paradas", help="Qtd de locais de embarque", width="small"),
                    "Cap.": st.column_config.NumberColumn("Lug.", width="small"),
                    "Ocupação": st.column_config.ProgressColumn(
                        "Ocupação", format="%d%%", min_value=0, max_value=100, width="medium"
                    ),
                    "Tempo": st.column_config.TextColumn("⏱️ Tempo", width="small"),
                    "KM": st.column_config.NumberColumn("KM", format="%.1f km"),
                    "Status": st.column_config.TextColumn("Status", width="small")
                }
            )