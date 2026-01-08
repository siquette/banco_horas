import streamlit as st
import pandas as pd
import sqlite3
import datetime
from datetime import date, time, datetime as dt, timedelta
import holidays
import plotly.express as px
from io import BytesIO
from typing import Tuple, Optional, Any

# --- CONFIGURAÇÕES GERAIS ---
st.set_page_config(page_title="Gestão de Tempo Analytics", layout="wide", page_icon="📊")
DB_FILE = "banco_horas_flex_v2.db"
META_DIARIA = 8.0 

# --- IMPORTS ADICIONAIS NECESSÁRIOS ---
from sqlalchemy import text # Para escrever SQL puro de forma segura

# --- CAMADA DE DADOS (NEON / POSTGRESQL) ---
def get_db_connection():
    """Recupera a conexão gerenciada do Streamlit"""
    # Procura no secrets.toml pela seção [connections.postgresql]
    return st.connection("postgresql", type="sql")

def init_db():
    conn = get_db_connection()
    with conn.session as s:
        # 1. Cria a tabela e SALVA (COMMIT) imediatamente
        # Isso garante que a tabela exista mesmo que os ALTER abaixo falhem
        s.execute(text('''
            CREATE TABLE IF NOT EXISTS registros (
                data TEXT PRIMARY KEY,
                entrada TEXT,
                almoco_ida TEXT,
                almoco_volta TEXT,
                saida TEXT,
                extra_inicio TEXT, 
                extra_fim TEXT,
                obs TEXT,
                feriado_manual INTEGER DEFAULT 0
            );
        '''))
        s.commit() # <--- O PULO DO GATO: Salva a tabela antes de tentar mudar ela

        # 2. Tenta adicionar colunas (Migrações) em transações isoladas
        # Se der erro (ex: coluna já existe), fazemos rollback apenas desse comando
        try:
            s.execute(text("ALTER TABLE registros ADD COLUMN extra_inicio TEXT;"))
            s.commit()
        except:
            s.rollback() # Limpa o erro se a coluna já existir

        try:
            s.execute(text("ALTER TABLE registros ADD COLUMN extra_fim TEXT;"))
            s.commit()
        except:
            s.rollback() # Limpa o erro se a coluna já existir

            
def salvar_registro(data, entrada, a_ida, a_volta, saida, ext_ini, ext_fim, obs, is_feriado):
    conn = get_db_connection()
    feriado_int = 1 if is_feriado else 0
    
    # Sintaxe PostgreSQL: usa :parametro em vez de ?
    # ON CONFLICT (data) é compatível com Postgres!
    sql = text('''
        INSERT INTO registros (data, entrada, almoco_ida, almoco_volta, saida, extra_inicio, extra_fim, obs, feriado_manual)
        VALUES (:data, :ent, :ai, :av, :sai, :ei, :ef, :obs, :fer)
        ON CONFLICT (data) DO UPDATE SET
            entrada = EXCLUDED.entrada,
            almoco_ida = EXCLUDED.almoco_ida,
            almoco_volta = EXCLUDED.almoco_volta,
            saida = EXCLUDED.saida,
            extra_inicio = EXCLUDED.extra_inicio,
            extra_fim = EXCLUDED.extra_fim,
            obs = EXCLUDED.obs,
            feriado_manual = EXCLUDED.feriado_manual;
    ''')
    
    params = {
        "data": data,
        "ent": str(entrada),
        "ai": str(a_ida),
        "av": str(a_volta),
        "sai": str(saida),
        "ei": str(ext_ini),
        "ef": str(ext_fim),
        "obs": obs,
        "fer": feriado_int
    }
    
    with conn.session as s:
        s.execute(sql, params)
        s.commit()

def carregar_dados():
    conn = get_db_connection()
    # ttl=0 garante que ele não use cache antigo, sempre pegue o dado fresco do banco
    return conn.query("SELECT * FROM registros", ttl=0)

# --- LÓGICA DE CÁLCULO (TIPADA E SEGURA) ---
def parse_db_time_to_delta(time_str: Optional[str]) -> timedelta:
    if pd.isna(time_str) or str(time_str).strip() in ['None', '']:
        return timedelta(0)
    try:
        parts = list(map(int, str(time_str).split(':')))
        if len(parts) == 2: return timedelta(hours=parts[0], minutes=parts[1])
        elif len(parts) == 3: return timedelta(hours=parts[0], minutes=parts[1], seconds=parts[2])
        return timedelta(0)
    except ValueError:
        return timedelta(0)

def calcular_delta_com_virada(inicio_str: Optional[str], fim_str: Optional[str]) -> float:
    t_ini = parse_db_time_to_delta(inicio_str)
    t_fim = parse_db_time_to_delta(fim_str)
    if t_ini == t_fim: return 0.0
    delta = t_fim - t_ini
    if delta.total_seconds() < 0:
        delta += timedelta(days=1)
    return delta.total_seconds() / 3600.0

def excluir_registro(data_str):
    """Remove um registro específico do banco de dados."""
    conn = get_db_connection()
    with conn.session as s:
        # Query SQL para deletar
        s.execute(
            text("DELETE FROM registros WHERE data = :d"), 
            {"d": data_str}
        )
        s.commit()

def processar_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty: return df
    df['data_dt'] = pd.to_datetime(df['data'], errors='coerce')
    cols_tempo = ['entrada', 'saida', 'almoco_ida', 'almoco_volta']
    for col in cols_tempo:
        df[f'td_{col}'] = df[col].apply(parse_db_time_to_delta)

    jornada_bruta = df['td_saida'] - df['td_entrada']
    pausa_almoco = df['td_almoco_volta'] - df['td_almoco_ida']
    df['horas_escritorio'] = (jornada_bruta - pausa_almoco).dt.total_seconds() / 3600.0
    
    df['horas_casa'] = df.apply(lambda x: calcular_delta_com_virada(x['extra_inicio'], x['extra_fim']), axis=1)
    
    df['horas_escritorio'] = df['horas_escritorio'].fillna(0.0)
    df['horas_casa'] = df['horas_casa'].fillna(0.0)
    df['total_trabalhado'] = df['horas_escritorio'] + df['horas_casa']
    
    cols_float = ['horas_escritorio', 'horas_casa', 'total_trabalhado']
    df[cols_float] = df[cols_float].astype(float)
    return df

def definir_meta(row: pd.Series) -> Tuple[float, str]:
    feriado_manual = int(row.get('feriado_manual', 0))
    data_str = str(row['data'])
    data_dt = row['data_dt']
    if feriado_manual == 1: return 0.0, "Folga/Feriado Manual"
    br_holidays = holidays.BR()
    if data_str in br_holidays: return 0.0, f"Feriado: {br_holidays.get(data_str)}"
    if pd.notnull(data_dt) and data_dt.weekday() >= 5: return 0.0, "Fim de Semana"
    return META_DIARIA, "Dia Útil"

# --- INTERFACE ---
init_db()

tab_lancamento, tab_analytics = st.tabs(["📝 Lançamento & Extrato", "📈 Análise Gerencial (BI)"])

# ---------------- ABA 1: LANÇAMENTO ----------------
with tab_lancamento:
    st.title("Apontamento Diário")
    col_input, col_view = st.columns([1, 2])
    
    with col_input:
        with st.container(border=True):
            st.subheader("Novo Registro")
            data_sel = st.date_input("Data", date.today())
            df_bd = carregar_dados()
            rec = df_bd[df_bd['data'] == str(data_sel)]
            d_ent, d_sai = time(9,0), time(18,0)
            d_ai, d_av = time(12,0), time(13,0)
            d_ext_ini, d_ext_fim = time(0,0), time(0,0)
            d_feriado, d_obs = False, ""

            if not rec.empty:
                st.info("Editando dia existente")
                try: d_feriado = True if rec.iloc[0]['feriado_manual'] == 1 else False
                except: d_feriado = False
                d_obs = rec.iloc[0]['obs']
                try:
                    h, m, s = map(int, rec.iloc[0]['extra_inicio'].split(':'))
                    d_ext_ini = time(h, m)
                    h, m, s = map(int, rec.iloc[0]['extra_fim'].split(':'))
                    d_ext_fim = time(h, m)
                except: pass

            is_feriado = st.checkbox("Feriado/Folga?", value=d_feriado)
            c1, c2 = st.columns(2)
            entrada = c1.time_input("Entrada", d_ent, disabled=is_feriado)
            saida = c2.time_input("Saída", d_sai, disabled=is_feriado)
            c3, c4 = st.columns(2)
            almoco_ida = c3.time_input("Almoço Ida", d_ai, disabled=is_feriado)
            almoco_volta = c4.time_input("Almoço Volta", d_av, disabled=is_feriado)
            st.caption("Trabalho Extra (Casa)")
            c5, c6 = st.columns(2)
            ext_ini = c5.time_input("Início Extra", d_ext_ini)
            ext_fim = c6.time_input("Fim Extra", d_ext_fim)
            obs = st.text_area("Obs", value=d_obs, height=68)
            
            if st.button("Salvar Registro", type="primary", use_container_width=True):
                salvar_registro(str(data_sel), entrada, almoco_ida, almoco_volta, saida, ext_ini, ext_fim, obs, is_feriado)
                st.toast("Registro Salvo com Sucesso!")
                st.rerun()

            st.markdown("---")
            with st.expander("🗑️ Excluir / Corrigir Data Errada"):
                st.warning("Cuidado: A exclusão é permanente.")
                
                # Carrega as datas que existem no banco para facilitar
                df_existentes = carregar_dados()
                if not df_existentes.empty:
                    # Cria uma lista de datas ordenada
                    lista_datas = df_existentes['data'].sort_values(ascending=False).tolist()
                    
                    data_para_excluir = st.selectbox(
                        "Selecione o dia para apagar:", 
                        options=lista_datas
                    )
                    
                    if st.button("🗑️ Apagar Registro Selecionado", type="secondary", use_container_width=True):
                        excluir_registro(data_para_excluir)
                        st.success(f"Registro de {data_para_excluir} apagado!")
                        st.rerun()
                else:
                    st.info("Não há registros para excluir.")

    with col_view:
        df = carregar_dados()
        if not df.empty:
            df = processar_dataframe(df)
            df[['meta', 'motivo']] = df.apply(definir_meta, axis=1, result_type='expand')
            df['saldo'] = df['total_trabalhado'] - df['meta']
            
            c_kpi1, c_kpi2, c_kpi3 = st.columns(3)
            saldo_total = df['saldo'].sum()
            c_kpi1.metric("Banco de Horas", f"{saldo_total:+.2f} h", delta_color="normal")
            c_kpi2.metric("Total Extra (Casa)", f"{df['horas_casa'].sum():.2f} h")
            c_kpi3.metric("Dias Registrados", len(df))
            
            st.dataframe(
                df[['data', 'horas_escritorio', 'horas_casa', 'total_trabalhado', 'saldo', 'motivo']]
                .sort_values('data', ascending=False)
                .style.format("{:.2f}", subset=['horas_escritorio', 'horas_casa', 'total_trabalhado', 'saldo'])
                .background_gradient(subset=['saldo'], cmap='RdYlGn', vmin=-2, vmax=2),
                width="stretch"
            )
            st.download_button("📥 Baixar Excel Completo", to_excel(df), "ponto_completo.xlsx")
        else:
            st.warning("Sem dados para exibir.")

# ---------------- ABA 2: ANALYTICS ----------------
with tab_analytics:
    st.header("Análise Descritiva e Tendências")
    
    df = carregar_dados()
    if not df.empty:
        df = processar_dataframe(df)
        df[['meta', 'motivo']] = df.apply(definir_meta, axis=1, result_type='expand')
        df['saldo'] = df['total_trabalhado'] - df['meta']
        
        st.markdown("### 🔍 Filtros de Análise")
        col_f1, col_f2 = st.columns(2)
        min_date = df['data_dt'].min().date()
        max_date = df['data_dt'].max().date()
        range_sel = col_f1.date_input("Período", value=(min_date, max_date))
        
        if isinstance(range_sel, tuple) and len(range_sel) == 2:
            start_d, end_d = range_sel
            mask = (df['data_dt'].dt.date >= start_d) & (df['data_dt'].dt.date <= end_d)
            df_filtered = df.loc[mask].copy()
        else:
            df_filtered = df.copy()

        st.markdown("---")

        # 1. HEATMAP
        st.subheader("📅 Mapa de Calor (Intensidade)")
        df_filtered['year'] = df_filtered['data_dt'].dt.year
        df_filtered['week'] = df_filtered['data_dt'].dt.isocalendar().week
        df_filtered['weekday_name'] = df_filtered['data_dt'].dt.strftime("%a")
        df_filtered['weekday_num'] = df_filtered['data_dt'].dt.weekday
        
        heatmap_data = df_filtered.groupby(['week', 'weekday_num', 'weekday_name'])['total_trabalhado'].sum().reset_index()
        fig_cal = px.density_heatmap(
            heatmap_data, x="week", y="weekday_name", z="total_trabalhado", 
            nbinsx=53, nbinsy=7, color_continuous_scale="Greens",
            title="Heatmap Semanal"
        )
        days_order = ["Sun", "Sat", "Fri", "Thu", "Wed", "Tue", "Mon"]
        fig_cal.update_yaxes(categoryorder='array', categoryarray=days_order)
        st.plotly_chart(fig_cal, use_container_width=True)

        # 2. NOVO GRÁFICO DE BARRAS (DIÁRIO)
        st.subheader("📊 Composição Diária (Regular + Extra)")
        # Ordenar cronologicamente para o gráfico de barras
        df_bar = df_filtered.sort_values('data_dt')
        fig_bar = px.bar(
            df_bar, 
            x='data', 
            y=['horas_escritorio', 'horas_casa'], 
            title="Horas Diárias: Escritório vs Casa",
            labels={'value': 'Horas', 'variable': 'Local', 'data': 'Data'},
            color_discrete_map={'horas_escritorio': '#3498DB', 'horas_casa': '#E67E22'}
        )
        # Linha de Meta (8h) para referência
        fig_bar.add_hline(y=META_DIARIA, line_dash="dot", line_color="red", annotation_text="Meta 8h")
        fig_bar.update_layout(hovermode="x unified", barmode='stack')
        st.plotly_chart(fig_bar, use_container_width=True)

        # 3. TENDÊNCIA E PIZZA
        c_graf1, c_graf2 = st.columns(2)
        with c_graf1:
            st.subheader("📈 Evolução do Banco")
            df_filtered = df_filtered.sort_values('data_dt')
            df_filtered['saldo_acumulado'] = df_filtered['saldo'].cumsum()
            fig_line = px.line(
                df_filtered, x='data', y='saldo_acumulado', markers=True,
                title="Saldo Acumulado", line_shape="spline"
            )
            fig_line.add_hline(y=0, line_dash="dot", line_color="gray")
            if not df_filtered.empty:
                cor_linha = "green" if df_filtered['saldo_acumulado'].iloc[-1] >= 0 else "red"
                fig_line.update_traces(line_color=cor_linha)
            st.plotly_chart(fig_line, use_container_width=True)
            
        with c_graf2:
            st.subheader("🥧 Distribuição Total")
            total_escritorio = df_filtered['horas_escritorio'].sum()
            total_casa = df_filtered['horas_casa'].sum()
            fig_pie = px.pie(
                names=["Escritório", "Extra Casa"],
                values=[total_escritorio, total_casa],
                hole=0.4,
                title="Proporção de Trabalho",
                color_discrete_sequence=['#3498DB', '#E67E22']
            )
            st.plotly_chart(fig_pie, use_container_width=True)
# 4. ANÁLISE DE COMPORTAMENTO (CORRELAÇÃO)
        st.subheader("🧩 Padrão de Comportamento: Chegada vs. Saldo")
        
        # Engenharia de Features para o Gráfico
        # Precisamos converter o horário de entrada (ex: 09:30) para número decimal (ex: 9.5) para plotar
        def time_to_float(t_str):
            if pd.isna(t_str): return None
            try:
                h, m, s = map(int, str(t_str).split(':'))
                return h + (m/60)
            except: return None

        df_filtered['entrada_num'] = df_filtered['entrada'].apply(time_to_float)
        
        # Gráfico de Dispersão
        fig_scatter = px.scatter(
            df_filtered, 
            x="entrada_num", 
            y="total_trabalhado", 
            color="saldo",
            size="total_trabalhado", # Bolinhas maiores = mais horas trabalhadas
            hover_data=['data', 'entrada', 'saida'],
            color_continuous_scale="RdYlGn", # Vermelho (devendo) -> Verde (crédito)
            title="Sua hora de chegada influencia quanto você trabalha?",
            labels={'entrada_num': 'Hora de Chegada (Decimal)', 'total_trabalhado': 'Total Trabalhado (h)'}
        )
        
        # Adiciona linhas de referência
        fig_scatter.add_vline(x=9.0, line_dash="dot", annotation_text="Chegada 09:00")
        fig_scatter.add_hline(y=META_DIARIA, line_dash="dot", annotation_text="Meta 8h")
        
        st.plotly_chart(fig_scatter, use_container_width=True)
        
        st.caption("💡 **Como ler:** Se os pontos estiverem muito espalhados na vertical, significa que sua hora de chegada não define sua produtividade. Se estiverem agrupados, você tem uma rotina rígida.")
        # 4. STATS E BOXPLOT
        st.markdown("---")
        c_stat1, c_stat2 = st.columns([1, 2])
        with c_stat1:
            st.subheader("📊 Resumo Estatístico")
            stats = df_filtered['total_trabalhado'].describe()
            st.markdown(f"""
            - **Média:** {stats['mean']:.2f} h
            - **Máx:** {stats['max']:.2f} h
            - **Min:** {stats['min']:.2f} h
            - **Desvio Padrão:** {stats['std']:.2f}
            """)
        with c_stat2:
            st.subheader("📦 Variabilidade")
            fig_box = px.box(
                df_filtered, y="total_trabalhado", points="all",
                title="Distribuição (Boxplot)"
            )
            fig_box.add_hline(y=META_DIARIA, line_dash="dot", annotation_text="Meta 8h")
            st.plotly_chart(fig_box, use_container_width=True)

    else:
        st.info("Insira dados na aba de Lançamento.")